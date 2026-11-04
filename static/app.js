'use strict';
document.addEventListener('DOMContentLoaded', () => {
    const $ = id => document.getElementById(id);
    let csrf = '', user = null, workspaces = [], members = [], tasks = [];
    let workspaceId = '', projectId = '', cursor = null, generation = 0;
    let boardController = null, aiEnabled = false;
    let meetings = [], meetingCursor = null, meetingId = '', meetingData = null;
    let meetingRequest = 0, meetingListRequest = 0, sourceFilename = '';
    let meetingSaveKey = '', meetingSaveBody = '', proposalSaveKey = '', proposalSaveBody = '';
    const show = (id, visible = true) => $(id).classList.toggle('hidden', !visible);
    const report = message => { $('notice').textContent = message; show('notice'); };
    const handle = fn => async event => {
        event?.preventDefault();
        try { await fn(event); } catch (error) { if (error.name !== 'AbortError') report(error.message); }
    };
    const role = () => workspaces.find(w => w.id === workspaceId)?.role;
    const canEdit = () => ['Admin', 'Member'].includes(role());
    const base = () => `/api/projects/${encodeURIComponent(projectId)}/tasks`;

    // Invitation secrets stay out of query strings, access logs and referrers.
    const incoming = new URLSearchParams(location.hash.slice(1)).get('invite');
    if (incoming && /^[A-Za-z0-9_-]{40,100}$/.test(incoming)) sessionStorage.setItem('taskiq_invite', incoming);
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    if (new URLSearchParams(location.search).has('auth_error')) {
        report('Google sign-in could not be completed. Please try again.');
        history.replaceState(null, '', '/');
    }

    function signedOut() {
        user = null; csrf = ''; workspaceId = ''; projectId = ''; tasks = []; members = [];
        generation++; boardController?.abort();
        $('edit-modal').close(); $('task-list').replaceChildren(); $('summary-content').textContent = '';
        resetMeetings();
        show('application', false); show('account', false); show('login-panel'); show('accept-invite', false);
    }

    async function api(path, {method = 'GET', body, signal} = {}) {
        const headers = {};
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        if (method !== 'GET') headers['X-CSRF-Token'] = csrf;
        const response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal, credentials: 'same-origin'});
        if (response.status === 401) { signedOut(); throw new Error('Your session ended. Please sign in again.'); }
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            const detail = typeof result.detail === 'string' ? result.detail : 'Please check the entered values.';
            throw new Error(detail);
        }
        return response.status === 204 ? null : response.json();
    }

    function options(select, rows, empty) {
        select.replaceChildren();
        if (!rows.length) select.add(new Option(empty, ''));
        rows.forEach(row => select.add(new Option(row.name, row.id)));
    }

    async function loadWorkspaces(preferred) {
        workspaces = await api('/api/workspaces');
        options($('workspace-select'), workspaces, 'No workspaces yet');
        workspaceId = workspaces.some(w => w.id === preferred) ? preferred : workspaces[0]?.id || '';
        $('workspace-select').value = workspaceId;
        await selectWorkspace();
    }

    async function selectWorkspace(preferredProjectId = '') {
        const epoch = ++generation;
        boardController?.abort();
        projectId = ''; tasks = []; members = []; cursor = null;
        resetMeetings();
        $('edit-modal').close(); $('member-list').replaceChildren(); $('invitation-list').replaceChildren();
        show('invite-result', false); show('project-content', false); show('empty-state');
        $('role-label').textContent = role() || '';
        document.querySelectorAll('.admin-only').forEach(el => el.classList.toggle('hidden', role() !== 'Admin'));
        document.querySelectorAll('.editor-only').forEach(el => el.classList.toggle('hidden', !canEdit()));
        document.querySelectorAll('.ai-only').forEach(el => el.classList.toggle('hidden', !aiEnabled));
        document.querySelector('[data-tab="tab-summary"]').classList.toggle('hidden', !canEdit() || !aiEnabled);
        show('team-section', !!workspaceId);
        if (!workspaceId) { options($('project-select'), [], 'Create a workspace first'); return; }
        const wid = workspaceId;
        const [projects, people] = await Promise.all([api(`/api/workspaces/${wid}/projects`), api(`/api/workspaces/${wid}/members`)]);
        if (epoch !== generation) return;
        members = people;
        options($('project-select'), projects, 'No projects yet');
        projectId = projects.some(p => p.id === preferredProjectId) ? preferredProjectId : projects[0]?.id || '';
        $('project-select').value = projectId;
        renderMembers();
        await Promise.all([selectProject(), loadInvitations(wid)]);
    }

    function button(label, action) {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'btn secondary'; b.textContent = label;
        b.addEventListener('click', handle(async () => { b.disabled = true; try { await action(); } finally { b.disabled = false; } }));
        return b;
    }

    function renderMembers() {
        $('member-list').replaceChildren();
        members.forEach(member => {
            const li = document.createElement('li');
            const label = document.createElement('span'); label.textContent = `${member.name} · ${member.email} · ${member.role}`; li.append(label);
            if (role() === 'Admin') {
                const select = document.createElement('select'); select.setAttribute('aria-label', `Role for ${member.name}`);
                ['Admin', 'Member', 'Viewer'].forEach(r => select.add(new Option(r, r)));
                select.value = member.role; li.append(select);
                li.append(button('Save role', async () => { await api(`/api/workspaces/${workspaceId}/members/${member.id}`, {method: 'PATCH', body: {role: select.value}}); await loadWorkspaces(workspaceId); }));
                li.append(button('Remove', async () => { if (confirm(`Remove ${member.name} from this workspace?`)) { await api(`/api/workspaces/${workspaceId}/members/${member.id}`, {method: 'DELETE'}); await loadWorkspaces(workspaceId); } }));
            }
            $('member-list').append(li);
        });
    }

    async function loadInvitations(wid) {
        if (role() !== 'Admin') return;
        const rows = await api(`/api/workspaces/${wid}/invitations`);
        if (wid !== workspaceId) return;
        $('invitation-list').replaceChildren();
        rows.forEach(invite => {
            const li = document.createElement('li'); const text = document.createElement('span');
            text.textContent = `${invite.email} · ${invite.role} · expires ${new Date(invite.expires_at * 1000).toLocaleDateString()}`;
            li.append(text, button('Revoke', async () => { await api(`/api/workspaces/${wid}/invitations/${invite.id}`, {method: 'DELETE'}); await loadInvitations(wid); }));
            $('invitation-list').append(li);
        });
    }

    async function selectProject() {
        generation++; boardController?.abort(); tasks = []; cursor = null;
        resetMeetings();
        $('edit-modal').close(); $('summary-content').textContent = ''; show('summary-panel', false);
        show('project-content', !!projectId); show('empty-state', !projectId);
        activateTab('tab-list');
        if (projectId) await fetchTasks();
    }

    async function fetchTasks(append = false) {
        if (!projectId) return;
        boardController?.abort(); boardController = new AbortController();
        const epoch = generation;
        const params = new URLSearchParams();
        if ($('filter-owner').value) params.set('owner', $('filter-owner').value);
        if ($('filter-status').value) params.set('status', $('filter-status').value);
        if ($('filter-priority').value) params.set('priority', $('filter-priority').value);
        if (append && cursor) params.set('before', cursor);
        const result = await api(`${base()}?${params}`, {signal: boardController.signal});
        if (epoch !== generation) return;
        tasks = append ? [...tasks, ...result.tasks] : result.tasks;
        cursor = result.next_cursor; renderTasks();
    }

    function renderTasks() {
        $('task-list').replaceChildren(); document.querySelectorAll('.kanban-dropzone').forEach(el => el.replaceChildren());
        if (!tasks.length) { const p = document.createElement('p'); p.textContent = 'No tasks found. Add a task or extract one from your notes.'; $('task-list').append(p); }
        tasks.forEach(task => {
            function card() {
                const div = document.createElement('article'); div.className = 'task-card';
                const status = document.createElement('span'); status.className = 'task-status'; status.textContent = `${task.status} · ${task.priority}`;
                const desc = document.createElement('p'); desc.className = 'task-desc'; desc.textContent = task.description;
                const meta = document.createElement('p'); meta.className = 'task-meta'; meta.textContent = `Due: ${task.due_date || 'No deadline'} · Owner: ${members.find(m => m.id === task.assignee_id)?.name || task.owner || 'Unassigned'}`;
                div.append(status, desc, meta);
                if (task.source) div.append(button(`Source: ${task.source.meeting_title}`, async () => {
                    activateTab('tab-meetings'); await loadMeetings();
                    await openMeeting(task.source.meeting_id, task.source.proposal_id);
                }));
                if (canEdit()) div.append(button('Edit', () => openTask(task)), button('Delete', async () => {
                    if (confirm('Delete this task?')) { const epoch = generation; await api(`${base()}/${task.id}`, {method: 'DELETE'}); if (epoch === generation) await fetchTasks(); }
                }));
                return div;
            }
            $('task-list').append(card());
            const kanban = card(); kanban.draggable = canEdit();
            kanban.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', String(task.id)));
            document.querySelector(`.kanban-col[data-status="${task.status}"] .kanban-dropzone`)?.append(kanban);
        });
        show('load-more', !!cursor); $('task-count').textContent = `${tasks.length} tasks loaded${cursor ? ' · More available' : ''}`;
    }

    function activateTab(id) {
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.toggle('active', el.dataset.tab === id));
        document.querySelectorAll('.tab-content').forEach(el => { el.classList.toggle('active', el.id === id); el.classList.toggle('hidden', el.id !== id); });
        const board = ['tab-list', 'tab-kanban'].includes(id);
        show('task-toolbar', board); show('task-pagination', board);
    }
    document.querySelectorAll('.tab-btn').forEach(el => el.addEventListener('click', handle(async () => { activateTab(el.dataset.tab); if (el.dataset.tab === 'tab-meetings') await loadMeetings(); })));
    document.querySelectorAll('.kanban-dropzone').forEach(zone => {
        zone.addEventListener('dragover', e => { if (canEdit()) e.preventDefault(); });
        zone.addEventListener('drop', handle(async e => {
            if (!canEdit()) return;
            const id = Number(e.dataTransfer.getData('text/plain'));
            if (!tasks.some(t => t.id === id)) return;
            const epoch = generation;
            await api(`${base()}/${id}`, {method: 'PUT', body: {status: zone.parentElement.dataset.status}});
            if (epoch === generation) await fetchTasks();
        }));
    });

    function openTask(task = {}) {
        $('task-dialog-title').textContent = task.id ? 'Edit task' : 'Add task';
        $('edit-id').value = task.id || ''; $('edit-description').value = task.description || '';
        $('edit-due-date').value = task.due_date || ''; $('edit-owner').value = task.owner || '';
        $('edit-priority').value = task.priority || 'Medium'; $('edit-status').value = task.status || 'To Do';
        $('edit-assignee').replaceChildren(new Option('Unassigned / named owner', ''));
        members.forEach(m => $('edit-assignee').add(new Option(m.name, m.id)));
        $('edit-assignee').value = task.assignee_id || ''; $('edit-owner').disabled = !!task.assignee_id;
        $('edit-modal').showModal();
    }
    $('edit-assignee').addEventListener('change', () => { $('edit-owner').disabled = !!$('edit-assignee').value; });
    $('close-modal').addEventListener('click', () => $('edit-modal').close());
    $('new-task').addEventListener('click', () => openTask());
    $('edit-form').addEventListener('submit', handle(async () => {
        const id = $('edit-id').value, epoch = generation;
        const body = {description: $('edit-description').value, due_date: $('edit-due-date').value, owner: $('edit-owner').value, assignee_id: $('edit-assignee').value || null, priority: $('edit-priority').value, status: $('edit-status').value};
        await api(id ? `${base()}/${id}` : base(), {method: id ? 'PUT' : 'POST', body});
        $('edit-modal').close(); if (epoch === generation) await fetchTasks();
    }));
    $('workspace-select').addEventListener('change', handle(async () => { workspaceId = $('workspace-select').value; await selectWorkspace(); }));
    $('project-select').addEventListener('change', handle(async () => { projectId = $('project-select').value; await selectProject(); }));
    $('workspace-form').addEventListener('submit', handle(async () => { const w = await api('/api/workspaces', {method: 'POST', body: {name: $('workspace-name').value}}); $('workspace-form').reset(); await loadWorkspaces(w.id); }));
    $('project-form').addEventListener('submit', handle(async () => { const p = await api(`/api/workspaces/${workspaceId}/projects`, {method: 'POST', body: {name: $('project-name').value}}); $('project-form').reset(); await selectWorkspace(p.id); }));
    $('profile-form').addEventListener('submit', handle(async () => { user = await api('/api/auth/me', {method: 'PATCH', body: {name: $('profile-name').value}}); $('account-name').textContent = user.name; report('Profile saved.'); if (workspaceId) await selectWorkspace(); }));
    $('invite-form').addEventListener('submit', handle(async () => { const wid = workspaceId; const result = await api(`/api/workspaces/${wid}/invitations`, {method: 'POST', body: {email: $('invite-email').value, role: $('invite-role').value}}); if (wid !== workspaceId) return; $('invite-link').value = result.invite_url; show('invite-result'); await loadInvitations(wid); }));
    $('copy-invite').addEventListener('click', handle(async () => { await navigator.clipboard.writeText($('invite-link').value); report('Invitation link copied.'); }));
    $('accept-invite').addEventListener('click', handle(async () => { const result = await api('/api/invitations/accept', {method: 'POST', body: {token: sessionStorage.getItem('taskiq_invite')}}); sessionStorage.removeItem('taskiq_invite'); show('invite-panel', false); await loadWorkspaces(result.workspace_id); report('You joined the workspace.'); }));
    $('dismiss-invite').addEventListener('click', () => { sessionStorage.removeItem('taskiq_invite'); show('invite-panel', false); });
    $('logout').addEventListener('click', handle(async () => { await api('/api/auth/logout', {method: 'POST'}); signedOut(); }));
    $('logout-all').addEventListener('click', handle(async () => { await api('/api/auth/logout-all', {method: 'POST'}); signedOut(); }));
    $('filters').addEventListener('submit', handle(() => fetchTasks()));
    $('load-more').addEventListener('click', handle(() => fetchTasks(true)));
    $('summary-btn').addEventListener('click', handle(async () => {
        const epoch = generation; $('summary-btn').disabled = true;
        try { const result = await api(`${base()}/summary`, {method: 'POST'}); if (epoch === generation) { $('summary-content').textContent = result.summary; show('summary-panel'); } }
        finally { $('summary-btn').disabled = false; }
    }));
    $('export-csv-btn').addEventListener('click', () => {
        const cell = value => { let text = String(value ?? ''); if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text; return '"' + text.replaceAll('"', '""') + '"'; };
        const rows = [['ID', 'Description', 'Due date', 'Owner', 'Priority', 'Status'], ...tasks.map(t => [t.id, t.description, t.due_date, members.find(m => m.id === t.assignee_id)?.name || t.owner, t.priority, t.status])];
        const url = URL.createObjectURL(new Blob(['\uFEFF' + rows.map(row => row.map(cell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}));
        const a = document.createElement('a'); a.href = url; a.download = 'taskiq-tasks.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });

    function meetingBase() { return `/api/projects/${encodeURIComponent(projectId)}/meetings`; }

    function resetMeetings() {
        meetingRequest++; meetingListRequest++; meetings = []; meetingCursor = null; meetingId = ''; meetingData = null;
        meetingSaveKey = ''; meetingSaveBody = ''; sourceFilename = '';
        $('proposal-modal').close(); $('meeting-form').reset();
        $('meeting-date').value = new Date().toLocaleDateString('en-CA');
        $('meeting-timezone').value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
        $('transcript-filename').textContent = ''; $('saved-source').replaceChildren(); $('proposal-list').replaceChildren(); $('meeting-list').replaceChildren();
        show('meeting-detail', false); show('more-meetings', false);
    }

    async function loadMeetings(append = false) {
        if (!projectId) return;
        const epoch = generation, request = ++meetingListRequest;
        const query = append && meetingCursor ? `?before=${encodeURIComponent(meetingCursor)}` : '';
        const result = await api(meetingBase() + query);
        if (epoch !== generation || request !== meetingListRequest) return;
        meetings = append ? [...meetings, ...result.meetings] : result.meetings; meetingCursor = result.next_cursor;
        $('meeting-list').replaceChildren();
        if (!meetings.length) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = 'No meetings saved for this project yet.'; $('meeting-list').append(p); }
        meetings.forEach(meeting => {
            const article = document.createElement('article'); article.className = 'meeting-card';
            const open = button(meeting.title, () => openMeeting(meeting.id)); open.classList.add('meeting-open');
            const info = document.createElement('p'); info.className = 'muted';
            const counts = meeting.proposal_counts;
            info.textContent = `${meeting.meeting_date} · ${meeting.source_type} · ${meeting.extraction_status} · ${counts.Pending || 0} pending / ${counts.Accepted || 0} accepted / ${counts.Rejected || 0} rejected`;
            article.append(open, info); $('meeting-list').append(article);
        });
        show('more-meetings', !!meetingCursor);
    }

    async function openMeeting(id, highlight = '') {
        const epoch = generation, request = ++meetingRequest;
        meetingId = id; meetingData = null;
        $('meeting-detail-title').textContent = 'Loading meeting…'; $('proposal-list').replaceChildren(); $('saved-source').replaceChildren();
        show('meeting-detail'); show('extract-proposals', false); show('new-proposal', false);
        const result = await api(`${meetingBase()}/${id}`);
        if (epoch !== generation || request !== meetingRequest) return;
        meetingData = result; renderMeeting();
        if (highlight) {
            const proposal = result.proposals.find(p => p.id === highlight);
            if (proposal) highlightSource(proposal);
        }
    }

    function renderMeetingHeader() {
        const meeting = meetingData.meeting, proposals = meetingData.proposals;
        $('meeting-detail-title').textContent = meeting.title;
        $('meeting-detail-meta').textContent = `${meeting.meeting_date} · ${meeting.timezone} · ${meeting.source_type}${meeting.source_filename ? ' · ' + meeting.source_filename : ''}`;
        $('meeting-status').textContent = `${meeting.extraction_status} · ${proposals.filter(p => p.status === 'Pending').length} pending · ${proposals.filter(p => p.status === 'Accepted').length} accepted · ${proposals.filter(p => p.status === 'Rejected').length} rejected`;
        const extracting = meeting.extraction_status === 'Extracting' && !meeting.retry_available;
        show('extract-proposals', canEdit() && aiEnabled && meeting.extraction_status !== 'Ready' && !extracting);
        $('extract-proposals').textContent = meeting.retry_available ? 'Retry task extraction' : 'Generate task proposals';
        show('new-proposal', canEdit() && !extracting);
        $('extraction-help').textContent = extracting ? 'Extraction is running. Refresh this meeting shortly; closing the page does not cancel it.' : !aiEnabled ? 'AI extraction is disabled here. You can still save sources and draft evidence-backed proposals manually.' : meeting.extraction_status === 'Ready' ? 'This source has a saved review set. Review pending proposals or add another manually; extraction retries will not duplicate it.' : 'AI suggestions are saved for review. No board tasks are created until you accept them.';
    }

    function renderMeeting() {
        renderMeetingHeader(); $('source-details').open = false;
        $('saved-source').textContent = meetingData.meeting.source_text;
        $('proposal-list').replaceChildren();
        if (!meetingData.proposals.length) {
            const p = document.createElement('p'); p.className = 'proposal-empty muted';
            p.textContent = meetingData.meeting.extraction_status === 'Ready' ? 'No actionable tasks were found. You can draft a proposal manually if needed.' : 'No proposals yet. Generate suggestions or draft a task from the saved source.';
            $('proposal-list').append(p);
        }
        meetingData.proposals.forEach(proposal => $('proposal-list').append(proposalCard(proposal)));
    }

    function highlightSource(proposal) {
        // Backend offsets count Unicode code points; JS string.slice counts UTF-16 units.
        const characters = Array.from(meetingData.meeting.source_text);
        const mark = document.createElement('mark'); mark.textContent = characters.slice(proposal.source_start, proposal.source_end).join('');
        $('saved-source').replaceChildren(document.createTextNode(characters.slice(0, proposal.source_start).join('')), mark, document.createTextNode(characters.slice(proposal.source_end).join('')));
        $('source-details').open = true; mark.scrollIntoView({block: 'center', behavior: 'instant'});
    }

    function proposalFields(form, proposal) {
        const controls = {};
        function field(name, label, multiline = false, maxLength = 120) {
            const wrapper = document.createElement('label'); wrapper.textContent = label;
            const input = document.createElement(multiline ? 'textarea' : 'input'); input.name = name; input.value = proposal[name] || ''; input.maxLength = maxLength;
            if (name === 'description') input.required = true;
            wrapper.append(input); form.append(wrapper); controls[name] = input;
        }
        field('description', 'Task description', true, 4000);
        const row = document.createElement('div'); row.className = 'proposal-fields';
        form.append(row);
        form = row;
        field('owner', 'Named owner'); field('due_date', 'Deadline as stated', false, 100);
        const priorityLabel = document.createElement('label'); priorityLabel.textContent = 'Priority';
        const priority = document.createElement('select'); priority.name = 'priority';
        ['High', 'Medium', 'Low'].forEach(value => priority.add(new Option(value, value))); priority.value = proposal.priority;
        priorityLabel.append(priority); row.append(priorityLabel); controls.priority = priority;
        const assigneeLabel = document.createElement('label'); assigneeLabel.textContent = 'Team assignee';
        const assignee = document.createElement('select'); assignee.name = 'assignee_id'; assignee.add(new Option('Unassigned / named owner', ''));
        members.forEach(member => assignee.add(new Option(member.name, member.id)));
        if (proposal.assignee_id && !members.some(member => member.id === proposal.assignee_id)) assignee.add(new Option('Former team member — change assignment', proposal.assignee_id));
        assignee.value = proposal.assignee_id || ''; controls.owner.disabled = !!assignee.value;
        assignee.addEventListener('change', () => { controls.owner.disabled = !!assignee.value; });
        assigneeLabel.append(assignee); row.append(assigneeLabel); controls.assignee_id = assignee;
        return () => ({description: controls.description.value, owner: controls.owner.value, due_date: controls.due_date.value, priority: controls.priority.value, assignee_id: controls.assignee_id.value || null});
    }

    async function withBusyForm(form, action) {
        const controls = [...form.querySelectorAll('input, textarea, select, button')];
        const states = controls.map(el => el.disabled); controls.forEach(el => { el.disabled = true; });
        try { return await action(); } finally { controls.forEach((el, i) => { el.disabled = states[i]; }); }
    }

    function upsertProposal(proposal) {
        const index = meetingData.proposals.findIndex(p => p.id === proposal.id);
        if (index >= 0) meetingData.proposals[index] = proposal; else meetingData.proposals.push(proposal);
        $('proposal-list').querySelector('.proposal-empty')?.remove();
        const previous = $('proposal-list').querySelector(`[data-proposal-id="${proposal.id}"]`);
        if (previous) previous.replaceWith(proposalCard(proposal)); else $('proposal-list').append(proposalCard(proposal));
        renderMeetingHeader();
    }

    function proposalCard(proposal) {
        const article = document.createElement('article'); article.className = 'proposal-card'; article.dataset.proposalId = proposal.id;
        const heading = document.createElement('h3'); heading.textContent = `${proposal.status} · ${proposal.origin === 'AI' ? 'AI suggestion' : 'Manually drafted'}`; article.append(heading);
        if (proposal.warnings.length) { const warnings = document.createElement('p'); warnings.className = 'review-warning'; warnings.textContent = proposal.warnings.join(' · '); article.append(warnings); }
        const quoteLabel = document.createElement('p'); quoteLabel.className = 'eyebrow'; quoteLabel.textContent = 'SUPPORTING SOURCE';
        const quote = document.createElement('blockquote'); quote.className = 'evidence-quote'; quote.textContent = proposal.source_quote;
        article.append(quoteLabel, quote, button('Highlight in saved source', () => highlightSource(proposal)));
        if (canEdit() && proposal.status === 'Pending') {
            const form = document.createElement('form'); const readFields = proposalFields(form, proposal);
            const actions = document.createElement('div'); actions.className = 'review-actions';
            const save = document.createElement('button'); save.type = 'button'; save.className = 'btn secondary'; save.textContent = 'Save edits';
            const accept = document.createElement('button'); accept.type = 'submit'; accept.className = 'btn primary'; accept.textContent = 'Accept task';
            const reject = document.createElement('button'); reject.type = 'button'; reject.className = 'btn secondary'; reject.textContent = 'Reject';
            const proposalPath = `${meetingBase()}/${meetingId}/proposals/${proposal.id}`, epoch = generation, selected = meetingId;
            save.addEventListener('click', handle(async () => {
                if (!form.reportValidity()) return;
                const edits = readFields();
                await withBusyForm(form, async () => {
                    const result = await api(proposalPath, {method: 'PATCH', body: {version: proposal.version, edits}});
                    if (epoch === generation && selected === meetingId && meetingData) { upsertProposal(result); report('Proposal edits saved. No board task has been created.'); }
                });
            }));
            async function review(action) {
                const body = {version: proposal.version, action}; if (action === 'accept') body.edits = readFields();
                await withBusyForm(form, async () => {
                    const result = await api(proposalPath + '/review', {method: 'POST', body});
                    if (epoch === generation && selected === meetingId && meetingData) {
                        upsertProposal(result.proposal); report(action === 'accept' ? 'Accepted. The task is now on your project board.' : 'Proposal rejected. No task was created.');
                        await Promise.all([loadMeetings(), fetchTasks()]);
                    }
                });
            }
            form.addEventListener('submit', handle(() => review('accept')));
            reject.addEventListener('click', handle(() => review('reject')));
            actions.append(accept, save, reject); form.append(actions); article.append(form);
        } else {
            const description = document.createElement('p'); description.className = 'review-description'; description.textContent = proposal.description;
            const fields = document.createElement('p'); fields.className = 'muted'; fields.textContent = `Owner: ${members.find(m => m.id === proposal.assignee_id)?.name || proposal.owner || 'Unassigned'} · Due: ${proposal.due_date || 'No deadline'} · ${proposal.priority} priority`;
            article.append(description, fields);
            if (proposal.reviewed_at) { const reviewed = document.createElement('p'); reviewed.className = 'muted'; reviewed.textContent = `Reviewed by ${members.find(m => m.id === proposal.reviewed_by)?.name || 'a former team member'} · ${new Date(proposal.reviewed_at * 1000).toLocaleString()}`; article.append(reviewed); }
            if (proposal.task_id) article.append(button('View project board', async () => { activateTab('tab-list'); await fetchTasks(); }));
            if (proposal.status === 'Accepted' && !proposal.task_id) { const deleted = document.createElement('p'); deleted.className = 'muted'; deleted.textContent = 'The accepted task was deleted; this review and its source remain in meeting history.'; article.append(deleted); }
        }
        return article;
    }

    $('open-meetings').addEventListener('click', handle(async () => { activateTab('tab-meetings'); await loadMeetings(); $('meeting-create').open = true; }));
    $('refresh-meetings').addEventListener('click', handle(() => loadMeetings()));
    $('more-meetings').addEventListener('click', handle(() => loadMeetings(true)));
    $('refresh-meeting').addEventListener('click', handle(() => meetingId && openMeeting(meetingId)));
    $('transcript-file').addEventListener('change', handle(async () => {
        const file = $('transcript-file').files[0]; if (!file) return;
        if (!/\.(txt|md|vtt|srt)$/i.test(file.name) || file.size > 80000) throw new Error('Choose a UTF-8 .txt, .md, .vtt, or .srt file up to 80 KB (20,000 characters).');
        let text;
        try { text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()); } catch { throw new Error('The transcript must be UTF-8 text.'); }
        if (text.length > 20000 || text.includes('\0')) throw new Error('Transcript must contain up to 20,000 text characters without binary data.');
        $('meeting-source').value = text; $('meeting-type').value = 'transcript'; sourceFilename = file.name; $('transcript-filename').textContent = file.name;
    }));
    $('meeting-type').addEventListener('change', () => { if ($('meeting-type').value === 'notes') { sourceFilename = ''; $('transcript-file').value = ''; $('transcript-filename').textContent = ''; } });
    $('meeting-form').addEventListener('submit', handle(async () => {
        const epoch = generation;
        const body = {title: $('meeting-title').value, meeting_date: $('meeting-date').value, timezone: $('meeting-timezone').value, source_type: $('meeting-type').value, source_filename: sourceFilename, source_text: $('meeting-source').value};
        const content = JSON.stringify(body); if (content !== meetingSaveBody) { meetingSaveKey = crypto.randomUUID(); meetingSaveBody = content; }
        body.request_id = meetingSaveKey;
        await withBusyForm($('meeting-form'), async () => {
            const result = await api(meetingBase(), {method: 'POST', body});
            if (epoch !== generation) return;
            $('meeting-form').reset(); sourceFilename = ''; meetingSaveKey = ''; meetingSaveBody = ''; $('transcript-filename').textContent = '';
            $('meeting-date').value = new Date().toLocaleDateString('en-CA'); $('meeting-timezone').value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
            $('meeting-create').open = false; meetingId = result.meeting.id; meetingData = result; meetingRequest++; renderMeeting(); show('meeting-detail'); await loadMeetings();
            report('Meeting source saved. Review proposals before creating any board tasks.');
        });
    }));
    $('extract-proposals').addEventListener('click', handle(async () => {
        if (!meetingData) return;
        const epoch = generation, selected = meetingId, path = `${meetingBase()}/${meetingId}/extract`;
        $('extract-proposals').disabled = true; $('new-proposal').disabled = true; $('meeting-status').textContent = 'Extracting proposals… Your board will remain unchanged until review.';
        try {
            const result = await api(path, {method: 'POST'});
            if (epoch === generation && selected === meetingId) { meetingData = result; renderMeeting(); await loadMeetings(); }
        } catch (error) {
            if (epoch === generation && selected === meetingId && user) await openMeeting(selected);
            throw error;
        } finally { $('extract-proposals').disabled = false; $('new-proposal').disabled = false; }
    }));
    $('new-proposal').addEventListener('click', () => {
        if (!meetingData) return;
        $('manual-proposal-form').reset(); proposalSaveKey = ''; proposalSaveBody = ''; $('proposal-modal').showModal();
    });
    $('cancel-proposal').addEventListener('click', () => $('proposal-modal').close());
    $('manual-proposal-form').addEventListener('submit', handle(async () => {
        const epoch = generation, selected = meetingId;
        const body = {description: $('manual-description').value, source_quote: $('manual-quote').value, owner: $('manual-owner').value, due_date: $('manual-due').value, priority: $('manual-priority').value};
        const content = JSON.stringify(body); if (content !== proposalSaveBody) { proposalSaveKey = crypto.randomUUID(); proposalSaveBody = content; }
        body.request_id = proposalSaveKey;
        await withBusyForm($('manual-proposal-form'), async () => {
            const result = await api(`${meetingBase()}/${meetingId}/proposals`, {method: 'POST', body});
            if (epoch !== generation || selected !== meetingId || !meetingData) return;
            $('proposal-modal').close(); meetingData.meeting.extraction_status = 'Ready'; upsertProposal(result); await loadMeetings();
            report('Proposal saved for review. Accept it to add a task to the board.');
        });
    }));

    handle(async () => {
        const cfg = await api('/api/auth/config'); aiEnabled = cfg.ai_enabled; show('google-login', cfg.google_enabled);
        $('login-help').textContent = cfg.google_enabled ? 'Google is used only to verify your identity. Calendar access is not requested.' : 'Google sign-in is not configured on this installation yet.';
        show('invite-panel', !!sessionStorage.getItem('taskiq_invite'));
        const response = await fetch('/api/auth/me', {credentials: 'same-origin'});
        if (response.status === 401) return;
        if (!response.ok) throw new Error('Could not load your account. Please refresh.');
        const data = await response.json(); user = data.user; csrf = data.csrf_token;
        $('account-name').textContent = user.name; $('profile-name').value = user.name;
        show('login-panel', false); show('account'); show('application'); show('accept-invite', !!sessionStorage.getItem('taskiq_invite'));
        await loadWorkspaces();
    })();
});
