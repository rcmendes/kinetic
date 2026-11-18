"""Capabilities: kinetic asks a store what it can do and falls back when it cannot."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kinetic import orbit_store, state, ticket_store


class BareStore:
    """A core-only store: no capabilities() and none of the optional methods."""

    def read(self, run_dir, st):
        return st.setdefault("tickets", {})

    def write(self, run_dir, st, before, after):
        return None


class CapabilityTest(unittest.TestCase):
    def test_local_store_has_no_capabilities(self):
        self.assertEqual(ticket_store.LocalTicketStore().capabilities(), frozenset())

    def test_has_is_false_for_a_store_without_the_capability(self):
        self.assertFalse(ticket_store.has(ticket_store.LocalTicketStore(), "review-states"))

    def test_has_tolerates_a_store_that_predates_capabilities(self):
        self.assertFalse(ticket_store.has(BareStore(), "review-states"))

    def test_has_rejects_an_unknown_capability_name(self):
        with self.assertRaisesRegex(ValueError, "unknown capability"):
            ticket_store.has(ticket_store.LocalTicketStore(), "review_states")

    def test_every_declared_capability_is_a_known_name(self):
        declared = orbit_store.make({}).capabilities()
        self.assertTrue(declared <= ticket_store.CAPABILITIES)
        self.assertIn("review-states", declared)


class ReviewFallbackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        state.init(self.run_dir, lane="feature", ref="x")
        state.set_ticket(self.run_dir, "01-api-a", repo="api")

    def test_review_calls_skip_a_store_without_review_states(self):
        with patch.object(ticket_store, "store_for", return_value=BareStore()):
            state.record_verify(self.run_dir, "01-api-a", 0, "/tmp/log", "api")
            self.assertEqual(state.record_review(self.run_dir, "01-api-a", "spec", "approve")["verdict"],
                             "approve")
