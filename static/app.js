'use strict';
document.addEventListener('DOMContentLoaded', () => {
    const $ = id => document.getElementById(id);
    let csrf = '', user = null, workspaces = [], members = [], tasks = [];
    let workspaceId = '', projectId = '', cursor = null, generation = 0;
    let boardController = null, aiEnabled = false;
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

    async function selectWorkspace() {
        const epoch = ++generation;
        boardController?.abort();
        projectId = ''; tasks = []; members = []; cursor = null;
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
        projectId = projects[0]?.id || '';
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
    }
    document.querySelectorAll('.tab-btn').forEach(el => el.addEventListener('click', () => activateTab(el.dataset.tab)));
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
    $('project-form').addEventListener('submit', handle(async () => { const p = await api(`/api/workspaces/${workspaceId}/projects`, {method: 'POST', body: {name: $('project-name').value}}); $('project-form').reset(); await selectWorkspace(); projectId = p.id; $('project-select').value = p.id; await selectProject(); }));
    $('profile-form').addEventListener('submit', handle(async () => { user = await api('/api/auth/me', {method: 'PATCH', body: {name: $('profile-name').value}}); $('account-name').textContent = user.name; report('Profile saved.'); if (workspaceId) await selectWorkspace(); }));
    $('invite-form').addEventListener('submit', handle(async () => { const wid = workspaceId; const result = await api(`/api/workspaces/${wid}/invitations`, {method: 'POST', body: {email: $('invite-email').value, role: $('invite-role').value}}); if (wid !== workspaceId) return; $('invite-link').value = result.invite_url; show('invite-result'); await loadInvitations(wid); }));
    $('copy-invite').addEventListener('click', handle(async () => { await navigator.clipboard.writeText($('invite-link').value); report('Invitation link copied.'); }));
    $('accept-invite').addEventListener('click', handle(async () => { const result = await api('/api/invitations/accept', {method: 'POST', body: {token: sessionStorage.getItem('taskiq_invite')}}); sessionStorage.removeItem('taskiq_invite'); show('invite-panel', false); await loadWorkspaces(result.workspace_id); report('You joined the workspace.'); }));
    $('dismiss-invite').addEventListener('click', () => { sessionStorage.removeItem('taskiq_invite'); show('invite-panel', false); });
    $('logout').addEventListener('click', handle(async () => { await api('/api/auth/logout', {method: 'POST'}); signedOut(); }));
    $('logout-all').addEventListener('click', handle(async () => { await api('/api/auth/logout-all', {method: 'POST'}); signedOut(); }));
    $('filters').addEventListener('submit', handle(() => fetchTasks()));
    $('load-more').addEventListener('click', handle(() => fetchTasks(true)));
    $('extract-btn').addEventListener('click', handle(async () => {
        const epoch = generation, button = $('extract-btn'); button.disabled = true; show('loading');
        try { await api(`${base()}/extract`, {method: 'POST', body: {text: $('notes-input').value}}); if (epoch === generation) { $('notes-input').value = ''; await fetchTasks(); } }
        finally { button.disabled = false; show('loading', false); }
    }));
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
