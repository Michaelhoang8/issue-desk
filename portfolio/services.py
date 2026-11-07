from datetime import date
from .db import rows
from .validation import ValidationError, NotFoundError, ConflictError, text, integer, choice

def ticket_create(db, data):
    title = text(data, 'title')
    description = text(data, 'description', 4000)
    priority = choice(data, 'priority', ('low', 'medium', 'high'))
    assignee, due_date = assignment(data)
    with db:
        ticket_id = db.execute('INSERT INTO tickets(title,description,priority,assignee,due_date) VALUES (?,?,?,?,?)', (title, description, priority, assignee, due_date)).lastrowid
        db.execute('INSERT INTO events(ticket_id,detail) VALUES (?,?)', (ticket_id, 'Created in backlog'))
    return {'id': ticket_id}

def ticket_move(db, ticket_id, data):
    status = choice(data, 'status', ('backlog', 'active', 'done'))
    version = integer(data, 'expected_version', 1) if 'expected_version' in data else None
    with db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT status,version FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if old is None:
            raise NotFoundError('Ticket not found')
        if old['status'] != status:
            if version is not None and version != old['version']:
                raise ConflictError('This issue changed since it was loaded. Review the latest issue before changing its status.')
            db.execute('UPDATE tickets SET status=?,version=version+1 WHERE id=?', (status, ticket_id))
            db.execute('INSERT INTO events(ticket_id,detail) VALUES (?,?)', (ticket_id, f"{old['status']} → {status}"))
    return {'id': ticket_id, 'status': status, 'version': old['version'] + (old['status'] != status)}

def ticket_archive(db, ticket_id, data):
    if set(data) - {'archived', 'expected_version'} or type(data.get('archived')) is not bool:
        raise ValidationError('Provide archived as a boolean and expected_version')
    version = integer(data, 'expected_version', 1)
    archived = int(data['archived'])
    with db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT archived,version FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if old is None:
            raise NotFoundError('Ticket not found')
        changed = old['archived'] != archived
        if changed:
            if version != old['version']:
                raise ConflictError('This issue changed since it was loaded. Review the latest issue before archiving or restoring it.')
            db.execute('UPDATE tickets SET archived=?,version=version+1 WHERE id=?', (archived, ticket_id))
            db.execute('INSERT INTO events(ticket_id,detail) VALUES (?,?)', (ticket_id, 'Archived issue' if archived else 'Restored issue'))
        return {'id': ticket_id, 'archived': bool(archived), 'version': old['version'] + changed}

def snapshot(db):
    if db.in_transaction:
        return _snapshot_rows(db)
    with db:
        db.execute('BEGIN')
        return _snapshot_rows(db)

def _snapshot_rows(db):
    return {'tickets': rows(db.execute('SELECT * FROM tickets ORDER BY id DESC')), 'events': rows(db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100'))}

def assignment(data):
    assignee = data.get('assignee', '')
    due = data.get('due_date', '')
    if not isinstance(assignee, str) or len(assignee) > 120:
        raise ValidationError('Assignee must be text of at most 120 characters')
    if not isinstance(due, str):
        raise ValidationError('Due date must be YYYY-MM-DD or empty')
    if due:
        try:
            if date.fromisoformat(due).isoformat() != due:
                raise ValueError()
        except ValueError:
            raise ValidationError('Due date must be YYYY-MM-DD or empty') from None
    return (assignee.strip(), due)

def ticket_details(db, ticket_id, data):
    allowed = {'title', 'description', 'priority', 'assignee', 'due_date', 'expected_version'}
    if set(data) - allowed:
        raise ValidationError('Unsupported issue detail field')
    if 'expected_version' in data:
        integer(data, 'expected_version', 1)
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if row is None:
            raise NotFoundError('Ticket not found')
        old = dict(row)
        merged = {**old, **data}
        assignee, due = assignment(merged)
        values = {'title': text(merged, 'title'), 'description': text(merged, 'description', 4000), 'priority': choice(merged, 'priority', ('low', 'medium', 'high')), 'assignee': assignee, 'due_date': due}
        changed = [name for name, value in values.items() if old[name] != value]
        if changed:
            if 'expected_version' in data and data['expected_version'] != old['version']:
                raise ConflictError('This issue changed since you opened it. Refresh and review the latest version before saving.')
            db.execute('UPDATE tickets SET title=?,description=?,priority=?,assignee=?,due_date=?,version=version+1 WHERE id=?', (*values.values(), ticket_id))
            db.execute('INSERT INTO events(ticket_id,detail) VALUES (?,?)', (ticket_id, 'Updated ' + ', '.join((name.replace('_', ' ') for name in changed))))
        return {'id': ticket_id, **values, 'version': old['version'] + bool(changed)}

def issue_audit_csv(db, batch_size=500):
    """Yield all issue events in recorded order, including archived issues."""
    from .data_apps import safe_csv
    cursor = db.execute('SELECT e.id,e.created_at,e.ticket_id,t.title,e.detail\n        FROM events e JOIN tickets t ON t.id=e.ticket_id ORDER BY e.id')
    try:
        yield safe_csv(['Event ID', 'Timestamp UTC', 'Issue ID', 'Current issue title', 'Event'], [])
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            yield safe_csv([], [[str(value) for value in row] for row in batch], include_header=False)
    finally:
        cursor.close()
