document.addEventListener('DOMContentLoaded', () => {
    const extractBtn = document.getElementById('extract-btn');
    const notesInput = document.getElementById('notes-input');
    const loadingEl = document.getElementById('loading');
    const taskListEl = document.getElementById('task-list');
    const summaryBtn = document.getElementById('summary-btn');
    const summaryPanel = document.getElementById('summary-panel');
    const summaryContent = document.getElementById('summary-content');
    const summaryLoading = document.getElementById('summary-loading');
    
    const filterOwner = document.getElementById('filter-owner');
    const filterStatus = document.getElementById('filter-status');
    const filterPriority = document.getElementById('filter-priority');
    const applyFiltersBtn = document.getElementById('apply-filters-btn');
    const exportCsvBtn = document.getElementById('export-csv-btn');

    // Modal elements
    const editModal = document.getElementById('edit-modal');
    const closeBtn = document.querySelector('.close-btn');
    const editForm = document.getElementById('edit-form');

    let currentTasks = [];

    // Tab Logic
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Remove active class from all
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            tabContents.forEach(c => c.classList.add('hidden'));
            
            // Add active class to clicked tab
            btn.classList.add('active');
            const target = document.getElementById(btn.dataset.tab);
            target.classList.add('active');
            target.classList.remove('hidden');
        });
    });

    // Initial load
    fetchTasks();

    // Setup drag and drop zones
    document.querySelectorAll('.kanban-dropzone').forEach(zone => {
        zone.addEventListener('dragover', e => {
            e.preventDefault();
            zone.classList.add('drag-over');
        });
        zone.addEventListener('dragleave', e => {
            zone.classList.remove('drag-over');
        });
        zone.addEventListener('drop', async e => {
            e.preventDefault();
            zone.classList.remove('drag-over');
            const taskId = e.dataTransfer.getData('text/plain');
            const newStatus = zone.parentElement.dataset.status;
            
            const task = currentTasks.find(t => t.id == taskId);
            if (task && task.status !== newStatus) {
                // Optimistic UI update
                task.status = newStatus;
                renderTasks(currentTasks);
                
                // Backend update
                try {
                    await fetch(`/api/tasks/${taskId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: newStatus })
                    });
                } catch (err) {
                    console.error("Failed to update status via drag", err);
                    fetchTasks(); // rollback on error
                }
            }
        });
    });

    summaryBtn.addEventListener('click', async () => {
        summaryLoading.classList.remove('hidden');
        summaryBtn.disabled = true;
        try {
            const res = await fetch('/api/tasks/summary');
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            
            // Simple markdown parser for the summary
            let html = data.summary
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.*?)\*/g, '<em>$1</em>')
                .replace(/\n\n/g, '</p><p>')
                .replace(/\n- (.*?)(?=\n|$)/g, '<li>$1</li>');
            
            if (html.includes('<li>')) {
                html = html.replace(/<li>.*<\/li>/s, match => `<ul>${match}</ul>`);
            }
            
            summaryContent.innerHTML = `<p>${html}</p>`;
            summaryPanel.classList.remove('hidden');
        } catch (e) {
            alert("Error generating summary: " + e.message);
        } finally {
            summaryLoading.classList.add('hidden');
            summaryBtn.disabled = false;
        }
    });

    extractBtn.addEventListener('click', async () => {
        const text = notesInput.value.trim();
        if (!text) return alert("Please paste some notes first.");

        extractBtn.disabled = true;
        loadingEl.classList.remove('hidden');

        try {
            const res = await fetch('/api/tasks/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Extraction failed');
            }
            notesInput.value = '';
            fetchTasks();
        } catch (e) {
            alert("Error extracting tasks: " + e.message);
        } finally {
            extractBtn.disabled = false;
            loadingEl.classList.add('hidden');
        }
    });

    applyFiltersBtn.addEventListener('click', () => {
        fetchTasks();
    });

    exportCsvBtn.addEventListener('click', () => {
        if (!currentTasks.length) return alert("No tasks to export");
        
        const headers = ["ID", "Description", "Due Date", "Owner", "Priority", "Status"];
        const rows = currentTasks.map(t => [
            t.id, 
            `"${t.description.replace(/"/g, '""')}"`, 
            t.due_date || "", 
            t.owner || "", 
            t.priority, 
            t.status
        ]);
        
        const csvContent = "data:text/csv;charset=utf-8," 
            + headers.join(",") + "\n" 
            + rows.map(e => e.join(",")).join("\n");
            
        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", "tasks.csv");
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });

    async function fetchTasks() {
        const params = new URLSearchParams();
        if (filterOwner.value) params.append('owner', filterOwner.value);
        if (filterStatus.value) params.append('status', filterStatus.value);
        if (filterPriority.value) params.append('priority', filterPriority.value);

        const url = `/api/tasks?${params.toString()}`;
        try {
            const res = await fetch(url);
            currentTasks = await res.json();
            renderTasks(currentTasks);
        } catch (e) {
            console.error("Failed to fetch tasks", e);
        }
    }

    function renderTasks(tasks) {
        taskListEl.innerHTML = '';
        document.querySelectorAll('.kanban-dropzone').forEach(z => z.innerHTML = '');

        if (tasks.length === 0) {
            taskListEl.innerHTML = '<p style="grid-column: 1/-1; text-align: center; color: var(--text-muted)">No tasks found.</p>';
            return;
        }

        tasks.forEach(t => {
            const pClass = t.priority === 'High' ? 'p-high' : (t.priority === 'Low' ? 'p-low' : 'p-medium');
            const innerHTML = `
                <div class="task-header">
                    <span class="task-status">${t.status}</span>
                    <span class="priority-indicator ${pClass}" title="Priority: ${t.priority}"></span>
                </div>
                <div class="task-desc">${t.description}</div>
                <div class="task-meta">
                    <div><strong>Due:</strong> ${t.due_date || 'N/A'}</div>
                    <div><strong>Owner:</strong> ${t.owner || 'Unassigned'}</div>
                </div>
                <div class="task-actions">
                    <button class="edit-btn" data-id="${t.id}">Edit</button>
                    <button class="delete-btn" data-id="${t.id}">Delete</button>
                </div>
            `;

            // List View Card
            const listCard = document.createElement('div');
            listCard.className = 'task-card';
            listCard.innerHTML = innerHTML;
            taskListEl.appendChild(listCard);

            // Kanban View Card
            const kanbanCard = document.createElement('div');
            kanbanCard.className = 'task-card';
            kanbanCard.draggable = true;
            kanbanCard.dataset.id = t.id;
            kanbanCard.innerHTML = innerHTML;
            
            kanbanCard.addEventListener('dragstart', e => {
                e.dataTransfer.setData('text/plain', t.id);
                e.dataTransfer.effectAllowed = 'move';
                setTimeout(() => kanbanCard.style.opacity = '0.5', 0);
            });
            kanbanCard.addEventListener('dragend', () => {
                kanbanCard.style.opacity = '1';
            });

            const dropzone = document.querySelector(`.kanban-col[data-status="${t.status}"] .kanban-dropzone`);
            if (dropzone) dropzone.appendChild(kanbanCard);
        });

        document.querySelectorAll('.edit-btn').forEach(btn => {
            btn.addEventListener('click', (e) => openEditModal(e.target.dataset.id));
        });
        document.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', (e) => deleteTask(e.target.dataset.id));
        });
    }

    async function deleteTask(id) {
        if (!confirm("Are you sure you want to delete this task?")) return;
        try {
            await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
            fetchTasks();
        } catch (e) {
            console.error(e);
        }
    }

    function openEditModal(id) {
        const task = currentTasks.find(t => t.id == id);
        if (!task) return;

        document.getElementById('edit-id').value = task.id;
        document.getElementById('edit-description').value = task.description;
        document.getElementById('edit-due-date').value = task.due_date || '';
        document.getElementById('edit-owner').value = task.owner || '';
        document.getElementById('edit-priority').value = task.priority || 'Medium';
        document.getElementById('edit-status').value = task.status || 'To Do';

        editModal.classList.remove('hidden');
    }

    closeBtn.addEventListener('click', () => {
        editModal.classList.add('hidden');
    });

    editForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('edit-id').value;
        const payload = {
            description: document.getElementById('edit-description').value,
            due_date: document.getElementById('edit-due-date').value,
            owner: document.getElementById('edit-owner').value,
            priority: document.getElementById('edit-priority').value,
            status: document.getElementById('edit-status').value,
        };

        try {
            await fetch(`/api/tasks/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            editModal.classList.add('hidden');
            fetchTasks();
        } catch (err) {
            console.error("Failed to update task", err);
        }
    });
});
