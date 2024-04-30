"""
Test desispec.workflow.calibration_selection
"""

import os
import unittest

import numpy as np
from astropy.table import Table, vstack

from desispec.workflow import queue

class TestWorkflowQueue(unittest.TestCase):
    """Test desispec.workflow.calibration_selection
    """

    def setUp(self):
        queue.clear_queue_state_cache()

    def test_queue_info_from_qids(self):
        """Test queue_info_from_qids"""
        qids = [1,10,2,5]
        qinfo = queue.queue_info_from_qids(qids, dry_run=True)
        self.assertEqual(list(qinfo['JOBID']), qids)

    def test_queue_state_cache(self):
        """Test queue state cache"""
        # Query qids to get state into cache
        qids = [1,10,2,5]
        qinfo = queue.queue_info_from_qids(qids, dry_run=True)

        # check cache matches state
        qstates = queue.get_queue_states_from_qids(qids, use_cache=True)
        self.assertEqual(list(qinfo['STATE']), list(qstates.values()))

        # update all states and check
        qinfo['STATE'] = 'FAILED'
        qinfo['STATE'][0] = 'PENDING'

        queue.update_queue_state_cache_from_table(qinfo)
        qstates = queue.get_queue_states_from_qids(qids, use_cache=True)
        self.assertEqual(list(qinfo['STATE']), list(qstates.values()))

        # update state of just one qid
        queue.update_queue_state_cache(10, 'COMPLETED')
        qstates = queue.get_queue_states_from_qids(qids, use_cache=True)
        self.assertEqual(qstates[1], 'PENDING')
        self.assertEqual(qstates[10], 'COMPLETED')
        self.assertEqual(qstates[2], 'FAILED')
        self.assertEqual(qstates[5], 'FAILED')

        # Asking for qids not in the cache should requery sacct for all of them.
        # Since this is dry run, that will also reset all back to COMPLETED.
        qids.append(100)
        qstates = queue.get_queue_states_from_qids(qids, use_cache=True, dry_run=True)
        for qid, state in qstates.items():
            self.assertEqual(state, 'COMPLETED', f'{qid=} {state=} not COMPLETED')


