import json
import csv
import io
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from portfolio import data_apps as d, services
from portfolio.db import initialize, connect, SCHEMA
from portfolio.validation import ValidationError, NotFoundError

class PersistentCapabilities(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        initialize(self.path)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_assignment_roundtrip_idempotent_audit_and_invalid_date(self):
        ticket = services.ticket_create(self.db, {'title': 'Ship', 'description': 'Review', 'priority': 'high', 'assignee': 'Ada', 'due_date': '2026-12-01'})['id']
        for _ in range(2):
            services.ticket_details(self.db, ticket, {'assignee': 'Bea', 'due_date': '2026-12-02'})
        result = services.snapshot(self.db)
        self.assertEqual(result['tickets'][0]['assignee'], 'Bea')
        self.assertEqual(len(result['events']), 2)
        with self.assertRaises(ValidationError):
            services.ticket_details(self.db, ticket, {'due_date': '2026-02-30'})
        with self.assertRaises(NotFoundError):
            services.ticket_details(self.db, 999, {})
if __name__ == '__main__':
    unittest.main()
