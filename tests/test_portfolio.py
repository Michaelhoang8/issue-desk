from http.client import HTTPConnection
import json
import csv
import io
import tempfile
import threading
import socket
import http.client
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from portfolio.db import initialize, connect
from portfolio import services as s
from portfolio.server import handler_for
from portfolio.validation import ValidationError, ConflictError

class DomainTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        initialize(self.path)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_ticket_lifecycle_audit_and_idempotent_status(self):
        item = s.ticket_create(self.db, {'title': 'Fix login', 'description': 'Handle expired session', 'priority': 'high'})['id']
        for status in ['active', 'done', 'done']:
            s.ticket_move(self.db, item, {'status': status})
        state = s.snapshot(self.db)
        self.assertEqual(state['tickets'][0]['status'], 'done')
        self.assertEqual(len(state['events']), 3)

    def test_versioned_issue_status_rejects_stale_changes_but_allows_noops(self):
        ticket = s.ticket_create(self.db, {'title': 'Status conflict', 'description': 'Context', 'priority': 'medium'})['id']
        result = s.ticket_move(self.db, ticket, {'status': 'active', 'expected_version': 1})
        self.assertEqual(result['version'], 2)
        self.assertEqual(s.ticket_move(self.db, ticket, {'status': 'active', 'expected_version': 1}), result)
        with self.assertRaises(ConflictError):
            s.ticket_move(self.db, ticket, {'status': 'done', 'expected_version': 1})
        with self.assertRaises(ValidationError):
            s.ticket_move(self.db, ticket, {'status': 'done', 'expected_version': True})
        self.assertEqual(len(s.snapshot(self.db)['events']), 2)
        s.ticket_details(self.db, ticket, {'title': 'Updated details', 'expected_version': 2})
        with self.assertRaises(ConflictError):
            s.ticket_move(self.db, ticket, {'status': 'done', 'expected_version': 2})
        self.assertEqual(s.ticket_move(self.db, ticket, {'status': 'done', 'expected_version': 3})['version'], 4)

    def test_concurrent_versioned_status_changes_have_one_winner(self):
        ticket = s.ticket_create(self.db, {'title': 'Race', 'description': 'Context', 'priority': 'medium'})['id']
        barrier = threading.Barrier(2)

        def move(status):
            db = connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    return s.ticket_move(db, ticket, {'status': status, 'expected_version': 1})['version']
                except ConflictError:
                    return 'conflict'
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(move, ['active', 'done']))
        self.assertCountEqual(results, [2, 'conflict'])
        self.assertEqual(len(s.snapshot(self.db)['events']), 2)

    def test_archive_restore_preserve_details_status_and_audit_idempotently(self):
        ticket = s.ticket_create(self.db, {'title': 'Keep history', 'description': 'Context', 'priority': 'high'})['id']
        s.ticket_move(self.db, ticket, {'status': 'done', 'expected_version': 1})
        archived = s.ticket_archive(self.db, ticket, {'archived': True, 'expected_version': 2})
        self.assertEqual(archived, {'id': ticket, 'archived': True, 'version': 3})
        self.assertEqual(s.ticket_archive(self.db, ticket, {'archived': True, 'expected_version': 2}), archived)
        row = s.snapshot(self.db)['tickets'][0]
        self.assertEqual((row['title'], row['description'], row['status'], row['archived']), ('Keep history', 'Context', 'done', 1))
        self.assertEqual(len(s.snapshot(self.db)['events']), 3)
        with self.assertRaises(ConflictError):
            s.ticket_archive(self.db, ticket, {'archived': False, 'expected_version': 2})
        restored = s.ticket_archive(self.db, ticket, {'archived': False, 'expected_version': 3})
        self.assertEqual(restored['version'], 4)
        row = s.snapshot(self.db)['tickets'][0]
        self.assertEqual((row['status'], row['archived']), ('done', 0))
        self.assertEqual(s.snapshot(self.db)['events'][0]['detail'], 'Restored issue')
        for body in ({'archived': 1, 'expected_version': 4}, {'archived': True}, {'archived': True, 'expected_version': True}, {'archived': True, 'expected_version': 4, 'title': 'No'}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                s.ticket_archive(self.db, ticket, body)

    def test_concurrent_archive_and_edit_have_one_winner(self):
        ticket = s.ticket_create(self.db, {'title': 'Race', 'description': 'Context', 'priority': 'medium'})['id']
        barrier = threading.Barrier(2)

        def mutate(archive):
            db = connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    result = s.ticket_archive(db, ticket, {'archived': True, 'expected_version': 1}) if archive else s.ticket_details(db, ticket, {'title': 'Updated', 'expected_version': 1})
                    return result['version']
                except ConflictError:
                    return 'conflict'
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mutate, [True, False]))
        self.assertCountEqual(results, [2, 'conflict'])
        self.assertEqual(len(s.snapshot(self.db)['events']), 2)

    def test_issue_edit_preserves_omitted_fields_and_audits_once(self):
        ticket = s.ticket_create(self.db, {'title': 'Original', 'description': 'Context', 'priority': 'medium', 'assignee': 'Ada', 'due_date': '2026-12-01'})['id']
        first = s.ticket_details(self.db, ticket, {'title': 'Revised', 'expected_version': 1})
        self.assertEqual(first['version'], 2)
        self.assertEqual((first['assignee'], first['due_date'], first['description']), ('Ada', '2026-12-01', 'Context'))
        replay = s.ticket_details(self.db, ticket, {'title': 'Revised', 'expected_version': 1})
        self.assertEqual(first, replay)
        self.assertEqual(len(s.snapshot(self.db)['events']), 2)
        with self.assertRaises(ValidationError):
            s.ticket_details(self.db, ticket, {'description': '', 'priority': 'high', 'expected_version': 2})
        self.assertEqual(s.snapshot(self.db)['tickets'][0]['priority'], 'medium')

    def test_stale_issue_edits_cannot_overwrite_newer_details_or_status(self):
        ticket = s.ticket_create(self.db, {'title': 'Original', 'description': 'Context', 'priority': 'medium'})['id']
        s.ticket_move(self.db, ticket, {'status': 'active'})
        with self.assertRaises(ConflictError):
            s.ticket_details(self.db, ticket, {'title': 'Stale', 'expected_version': 1})
        latest = s.ticket_details(self.db, ticket, {'title': 'Current', 'expected_version': 2})
        self.assertEqual(latest['version'], 3)
        with self.assertRaises(ConflictError):
            s.ticket_details(self.db, ticket, {'assignee': 'Lost update', 'expected_version': 2})
        with self.assertRaises(ValidationError):
            s.ticket_details(self.db, ticket, {'expected_version': True})
        current = s.snapshot(self.db)['tickets'][0]
        self.assertEqual((current['title'], current['assignee'], current['status']), ('Current', '', 'active'))

    def test_issue_audit_export_is_complete_ordered_and_spreadsheet_safe(self):
        ticket = s.ticket_create(self.db, {'title': '=SYNTHETIC_FORMULA()', 'description': 'Audit fixture', 'priority': 'low'})['id']
        for index in range(1002):
            s.ticket_move(self.db, ticket, {'status': 'active' if index % 2 == 0 else 'backlog'})
        s.ticket_archive(self.db, ticket, {'archived': True, 'expected_version': 1003})
        chunks = list(s.issue_audit_csv(self.db))
        self.assertEqual(len(chunks), 4)
        rows = list(csv.DictReader(io.StringIO(''.join(chunks))))
        self.assertEqual(len(rows), 1004)
        self.assertEqual(len(s.snapshot(self.db)['events']), 100)
        self.assertEqual(rows[0]['Event'], 'Created in backlog')
        self.assertEqual(rows[-1]['Event'], 'Archived issue')
        self.assertEqual([int(row['Event ID']) for row in rows], list(range(1, 1005)))
        self.assertTrue(all((row['Current issue title'] == "'=SYNTHETIC_FORMULA()" for row in rows)))
        self.assertTrue(all((row['Issue ID'] == str(ticket) for row in rows)))

    def test_issue_audit_export_keeps_one_snapshot_during_concurrent_edits(self):
        ticket = s.ticket_create(self.db, {'title': 'Original title', 'description': 'Audit fixture', 'priority': 'low'})['id']
        s.ticket_move(self.db, ticket, {'status': 'active'})
        stream = s.issue_audit_csv(self.db, batch_size=1)
        chunks = [next(stream), next(stream)]
        other = connect(self.path)
        try:
            s.ticket_details(other, ticket, {'title': 'New title', 'expected_version': 2})
        finally:
            other.close()
        chunks.extend(stream)
        old = list(csv.DictReader(io.StringIO(''.join(chunks))))
        new = list(csv.DictReader(io.StringIO(''.join(s.issue_audit_csv(self.db)))))
        self.assertEqual(len(old), 2)
        self.assertTrue(all((row['Current issue title'] == 'Original title' for row in old)))
        self.assertEqual(len(new), 3)
        self.assertTrue(all((row['Current issue title'] == 'New title' for row in new)))

    def test_issue_audit_empty_export_has_headers(self):
        rows = list(csv.reader(io.StringIO(''.join(s.issue_audit_csv(self.db)))))
        self.assertEqual(rows, [['Event ID', 'Timestamp UTC', 'Issue ID', 'Current issue title', 'Event']])

    def test_persistence_across_connections_and_sql_input(self):
        title = "Robert'); DROP TABLE tickets; --"
        s.ticket_create(self.db, {'title': title, 'description': '<script>alert(1)</script>', 'priority': 'low'})
        other = connect(self.path)
        try:
            self.assertEqual(s.snapshot(other)['tickets'][0]['title'], title)
        finally:
            other.close()
if __name__ == '__main__':
    unittest.main()
