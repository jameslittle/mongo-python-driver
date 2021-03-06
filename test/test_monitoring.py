# Copyright 2015 MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import time
import warnings

sys.path[0:0] = [""]

from bson.objectid import ObjectId
from bson.py3compat import text_type
from bson.son import SON
from pymongo import CursorType, monitoring
from pymongo.command_cursor import CommandCursor
from pymongo.errors import NotMasterError, OperationFailure
from pymongo.write_concern import WriteConcern
from test import unittest, IntegrationTest, client_context, client_knobs
from test.utils import single_client


class EventListener(monitoring.Subscriber):

    def __init__(self):
        self.results = {}

    def started(self, event):
        self.results['started'] = event

    def succeeded(self, event):
        self.results['succeeded'] = event

    def failed(self, event):
        self.results['failed'] = event


class TestCommandMonitoring(IntegrationTest):

    @classmethod
    def setUpClass(cls):
        cls.listener = EventListener()
        cls.saved_subscribers = monitoring._SUBSCRIBERS
        monitoring.subscribe(cls.listener)
        super(TestCommandMonitoring, cls).setUpClass()

    @classmethod
    def tearDownClass(cls):
        monitoring._SUBSCRIBERS = cls.saved_subscribers

    def tearDown(self):
        self.listener.results = {}

    def test_started_simple(self):
        self.client.pymongo_test.command('ismaster')
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(SON([('ismaster', 1)]), started.command)
        self.assertEqual('ismaster', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))

    def test_succeeded_simple(self):
        self.client.pymongo_test.command('ismaster')
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertEqual('ismaster', succeeded.command_name)
        self.assertEqual(self.client.address, succeeded.connection_id)
        self.assertEqual(1, succeeded.reply.get('ok'))
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertTrue(isinstance(succeeded.duration_micros, int))

    def test_failed_simple(self):
        try:
            self.client.pymongo_test.command('oops!')
        except OperationFailure:
            pass
        results = self.listener.results
        started = results.get('started')
        failed = results.get('failed')
        self.assertIsNone(results.get('succeeded'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertTrue(
            isinstance(failed, monitoring.CommandFailedEvent))
        self.assertEqual('oops!', failed.command_name)
        self.assertEqual(self.client.address, failed.connection_id)
        self.assertEqual(0, failed.failure.get('ok'))
        self.assertTrue(isinstance(failed.request_id, int))
        self.assertTrue(isinstance(failed.duration_micros, int))

    def test_find_one(self):
        self.client.pymongo_test.test.find_one()
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('find', 'test'),
                 ('filter', {}),
                 ('limit', -1),
                 ('singleBatch', True)]),
            started.command)
        self.assertEqual('find', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))

    def test_find_and_get_more(self):
        self.client.pymongo_test.test.drop()
        self.client.pymongo_test.test.insert_many([{} for _ in range(10)])
        self.listener.results = {}
        cursor = self.client.pymongo_test.test.find(
            projection={'_id': False},
            batch_size=4)
        for _ in range(4):
            next(cursor)
        cursor_id = cursor.cursor_id
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('find', 'test'),
                 ('filter', {}),
                 ('projection', {'_id': False}),
                 ('batchSize', 4)]),
            started.command)
        self.assertEqual('find', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('find', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(cursor.address, succeeded.connection_id)
        expected_result = {
            'cursor': {'id': cursor_id,
                       'ns': 'pymongo_test.test',
                       'firstBatch': [{} for _ in range(4)]},
            'ok': 1}
        self.assertEqual(expected_result, succeeded.reply)

        self.listener.results = {}
        # Next batch. Exhausting the cursor could cause a getMore
        # that returns id of 0 and no results.
        next(cursor)
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('getMore', cursor_id),
                 ('collection', 'test'),
                 ('batchSize', 4)]),
            started.command)
        self.assertEqual('getMore', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('getMore', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(cursor.address, succeeded.connection_id)
        expected_result = {
            'cursor': {'id': cursor_id,
                       'ns': 'pymongo_test.test',
                       'nextBatch': [{} for _ in range(4)]},
            'ok': 1}
        self.assertEqual(expected_result, succeeded.reply)

    def test_find_with_explain(self):
        self.client.pymongo_test.test.drop()
        self.client.pymongo_test.test.insert_one({})
        self.listener.results = {}
        res = self.client.pymongo_test.test.find().explain()
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('explain', SON([('find', 'test'),
                                  ('filter', {})]))]),
            started.command)
        self.assertEqual('explain', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('explain', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(self.client.address, succeeded.connection_id)
        self.assertEqual(res, succeeded.reply)

    @client_context.require_version_min(2, 6, 0)
    def test_command_and_get_more(self):
        self.client.pymongo_test.test.drop()
        self.client.pymongo_test.test.insert_many(
            [{'x': 1} for _ in range(10)])
        self.listener.results = {}
        cursor = self.client.pymongo_test.test.aggregate(
            [{'$project': {'_id': False, 'x': 1}}], batchSize=4)
        for _ in range(4):
            next(cursor)
        cursor_id = cursor.cursor_id
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('aggregate', 'test'),
                 ('pipeline', [{'$project': {'_id': False, 'x': 1}}]),
                 ('cursor', {'batchSize': 4})]),
            started.command)
        self.assertEqual('aggregate', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('aggregate', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(cursor.address, succeeded.connection_id)
        expected_cursor = {'id': cursor_id,
                           'ns': 'pymongo_test.test',
                           'firstBatch': [{'x': 1} for _ in range(4)]}
        self.assertEqual(expected_cursor, succeeded.reply.get('cursor'))

        self.listener.results = {}
        next(cursor)
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('getMore', cursor_id),
                 ('collection', 'test'),
                 ('batchSize', 4)]),
            started.command)
        self.assertEqual('getMore', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('getMore', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(cursor.address, succeeded.connection_id)
        expected_result = {
            'cursor': {'id': cursor_id,
                       'ns': 'pymongo_test.test',
                       'nextBatch': [{'x': 1} for _ in range(4)]},
            'ok': 1}
        self.assertEqual(expected_result, succeeded.reply)

    def test_get_more_failure(self):
        address = self.client.address
        coll = self.client.pymongo_test.test
        cursor_doc = {"id": 12345, "firstBatch": [], "ns": coll.full_name}
        cursor = CommandCursor(coll, cursor_doc, address)
        try:
            next(cursor)
        except Exception:
            pass
        results = self.listener.results
        started = results.get('started')
        self.assertIsNone(results.get('succeeded'))
        failed = results.get('failed')
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('getMore', 12345),
                 ('collection', 'test')]),
            started.command)
        self.assertEqual('getMore', started.command_name)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(failed, monitoring.CommandFailedEvent))
        self.assertTrue(isinstance(failed.duration_micros, int))
        self.assertEqual('getMore', failed.command_name)
        self.assertTrue(isinstance(failed.request_id, int))
        self.assertEqual(cursor.address, failed.connection_id)
        self.assertEqual(0, failed.failure.get("ok"))

    @client_context.require_replica_set
    def test_not_master_error(self):
        address = next(iter(self.client.secondaries))
        client = single_client(*address)
        # Clear authentication command results from the listener.
        client.admin.command('ismaster')
        self.listener.results = {}
        error = None
        try:
            client.pymongo_test.test.find_one_and_delete({})
        except NotMasterError as exc:
            error = exc.errors
        results = self.listener.results
        started = results.get('started')
        failed = results.get('failed')
        self.assertIsNone(results.get('succeeded'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertTrue(
            isinstance(failed, monitoring.CommandFailedEvent))
        self.assertEqual('findAndModify', failed.command_name)
        self.assertEqual(address, failed.connection_id)
        self.assertEqual(0, failed.failure.get('ok'))
        self.assertTrue(isinstance(failed.request_id, int))
        self.assertTrue(isinstance(failed.duration_micros, int))
        self.assertEqual(error, failed.failure)

    @client_context.require_no_mongos
    def test_exhaust(self):
        self.client.pymongo_test.test.drop()
        self.client.pymongo_test.test.insert_many([{} for _ in range(10)])
        self.listener.results = {}
        cursor = self.client.pymongo_test.test.find(
            projection={'_id': False},
            batch_size=5,
            cursor_type=CursorType.EXHAUST)
        next(cursor)
        cursor_id = cursor.cursor_id
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('find', 'test'),
                 ('filter', {}),
                 ('projection', {'_id': False}),
                 ('batchSize', 5)]),
            started.command)
        self.assertEqual('find', started.command_name)
        self.assertEqual(cursor.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('find', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(cursor.address, succeeded.connection_id)
        expected_result = {
            'cursor': {'id': cursor_id,
                       'ns': 'pymongo_test.test',
                       'firstBatch': [{} for _ in range(5)]},
            'ok': 1}
        self.assertEqual(expected_result, succeeded.reply)

        self.listener.results = {}
        for _ in cursor:
            pass
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertTrue(
            isinstance(started, monitoring.CommandStartedEvent))
        self.assertEqual(
            SON([('getMore', cursor_id),
                 ('collection', 'test'),
                 ('batchSize', 5)]),
            started.command)
        self.assertEqual('getMore', started.command_name)
        self.assertEqual(cursor.address, started.connection_id)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertTrue(isinstance(started.request_id, int))
        self.assertTrue(
            isinstance(succeeded, monitoring.CommandSucceededEvent))
        self.assertTrue(isinstance(succeeded.duration_micros, int))
        self.assertEqual('getMore', succeeded.command_name)
        self.assertTrue(isinstance(succeeded.request_id, int))
        self.assertEqual(cursor.address, succeeded.connection_id)
        expected_result = {
            'cursor': {'id': 0,
                       'ns': 'pymongo_test.test',
                       'nextBatch': [{} for _ in range(5)]},
            'ok': 1}
        self.assertEqual(expected_result, succeeded.reply)

    def test_kill_cursors(self):
        with client_knobs(kill_cursor_frequency=0.01):
            self.client.pymongo_test.test.drop()
            self.client.pymongo_test.test.insert_many([{} for _ in range(10)])
            cursor = self.client.pymongo_test.test.find().batch_size(5)
            next(cursor)
            cursor_id = cursor.cursor_id
            self.listener.results = {}
            cursor.close()
            time.sleep(2)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertTrue(
                isinstance(started, monitoring.CommandStartedEvent))
            # There could be more than one cursor_id here depending on
            # when the thread last ran.
            self.assertIn(cursor_id, started.command['cursors'])
            self.assertEqual('killCursors', started.command_name)
            self.assertEqual(cursor.address, started.connection_id)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertTrue(isinstance(started.request_id, int))
            self.assertTrue(
                isinstance(succeeded, monitoring.CommandSucceededEvent))
            self.assertTrue(isinstance(succeeded.duration_micros, int))
            self.assertEqual('killCursors', succeeded.command_name)
            self.assertTrue(isinstance(succeeded.request_id, int))
            self.assertEqual(cursor.address, succeeded.connection_id)
            # There could be more than one cursor_id here depending on
            # when the thread last ran.
            self.assertIn(cursor_id, succeeded.reply['cursorsUnknown'])

    def test_non_bulk_writes(self):
        coll = self.client.pymongo_test.test
        coll.drop()

        # Implied write concern insert_one
        res = coll.insert_one({'x': 1})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('insert', coll.name),
                        ('documents', [{'_id': res.inserted_id, 'x': 1}]),
                        ('ordered', True)])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('insert', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(1, reply.get('n'))

        # Unacknowledged insert_one
        coll = coll.with_options(write_concern=WriteConcern(w=0))
        res = coll.insert_one({'x': 1})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('insert', coll.name),
                        ('documents', [{'_id': res.inserted_id, 'x': 1}]),
                        ('ordered', True)])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('insert', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        # The reply document is supposed to be None.
        self.assertIsNone(succeeded.reply)

        # Explicit write concern insert_one
        coll = coll.with_options(write_concern=WriteConcern(w=1))
        res = coll.insert_one({'x': 1})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('insert', coll.name),
                        ('documents', [{'_id': res.inserted_id, 'x': 1}]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('insert', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(1, reply.get('n'))

        # delete_many
        res = coll.delete_many({'x': 1})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('delete', coll.name),
                        ('deletes', [SON([('q', {'x': 1}),
                                          ('limit', 0)])]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('delete', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(3, reply.get('n'))

        # replace_one
        oid = ObjectId()
        res = coll.replace_one({'_id': oid}, {'_id': oid, 'x': 1}, upsert=True)
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('update', coll.name),
                        ('updates', [SON([('q', {'_id': oid}),
                                          ('u', {'_id': oid, 'x': 1}),
                                          ('multi', False),
                                          ('upsert', True)])]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('update', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(1, reply.get('n'))
        self.assertEqual(0, reply.get('nModified'))
        self.assertEqual([{'index': 0, '_id': oid}], reply.get('upserted'))

        # update_one
        res = coll.update_one({'x': 1}, {'$inc': {'x': 1}})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('update', coll.name),
                        ('updates', [SON([('q', {'x': 1}),
                                          ('u', {'$inc': {'x': 1}}),
                                          ('multi', False),
                                          ('upsert', False)])]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('update', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(1, reply.get('n'))
        self.assertEqual(1, reply.get('nModified'))

        # update_many
        res = coll.update_many({'x': 2}, {'$inc': {'x': 1}})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('update', coll.name),
                        ('updates', [SON([('q', {'x': 2}),
                                          ('u', {'$inc': {'x': 1}}),
                                          ('multi', True),
                                          ('upsert', False)])]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('update', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(1, reply.get('n'))
        self.assertEqual(1, reply.get('nModified'))

        # delete_one
        res = coll.delete_one({'x': 3})
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('delete', coll.name),
                        ('deletes', [SON([('q', {'x': 3}),
                                          ('limit', 1)])]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('delete', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(1, reply.get('n'))

        self.assertEqual(0, coll.count())

        # write errors
        coll.insert_one({'_id': 1})
        try:
            coll.insert_one({'_id': 1})
        except OperationFailure:
            pass
        results = self.listener.results
        started = results.get('started')
        succeeded = results.get('succeeded')
        self.assertIsNone(results.get('failed'))
        self.assertIsInstance(started, monitoring.CommandStartedEvent)
        expected = SON([('insert', coll.name),
                        ('documents', [{'_id': 1}]),
                        ('ordered', True),
                        ('writeConcern', {'w': 1})])
        self.assertEqual(expected, started.command)
        self.assertEqual('pymongo_test', started.database_name)
        self.assertEqual('insert', started.command_name)
        self.assertIsInstance(started.request_id, int)
        self.assertEqual(self.client.address, started.connection_id)
        self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
        self.assertIsInstance(succeeded.duration_micros, int)
        self.assertEqual(started.command_name, succeeded.command_name)
        self.assertEqual(started.request_id, succeeded.request_id)
        self.assertEqual(started.connection_id, succeeded.connection_id)
        reply = succeeded.reply
        self.assertEqual(1, reply.get('ok'))
        self.assertEqual(0, reply.get('n'))
        errors = reply.get('writeErrors')
        self.assertIsInstance(errors, list)
        error = errors[0]
        self.assertEqual(0, error.get('index'))
        self.assertIsInstance(error.get('code'), int)
        self.assertIsInstance(error.get('errmsg'), text_type)

    def test_legacy_writes(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            coll = self.client.pymongo_test.test
            coll.drop()

            # Implied write concern insert
            _id = coll.insert({'x': 1})
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('insert', coll.name),
                            ('documents', [{'_id': _id, 'x': 1}]),
                            ('ordered', True)])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('insert', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(1, reply.get('n'))

            # Unacknowledged insert
            _id = coll.insert({'x': 1}, w=0)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('insert', coll.name),
                            ('documents', [{'_id': _id, 'x': 1}]),
                            ('ordered', True)])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('insert', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            # The reply document is supposed to be None.
            self.assertIsNone(succeeded.reply)

            # Explicit write concern insert
            _id = coll.insert({'x': 1}, w=1)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('insert', coll.name),
                            ('documents', [{'_id': _id, 'x': 1}]),
                            ('ordered', True),
                            ('writeConcern', {'w': 1})])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('insert', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(1, reply.get('n'))

            # remove all
            coll.remove({'x': 1}, w=1)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('delete', coll.name),
                            ('deletes', [SON([('q', {'x': 1}),
                                              ('limit', 0)])]),
                            ('ordered', True),
                            ('writeConcern', {'w': 1})])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('delete', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(3, reply.get('n'))

            # upsert
            oid = ObjectId()
            coll.update({'_id': oid}, {'_id': oid, 'x': 1}, upsert=True, w=1)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('update', coll.name),
                            ('updates', [SON([('q', {'_id': oid}),
                                              ('u', {'_id': oid, 'x': 1}),
                                              ('multi', False),
                                              ('upsert', True)])]),
                            ('ordered', True),
                            ('writeConcern', {'w': 1})])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('update', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(1, reply.get('n'))
            self.assertEqual(0, reply.get('nModified'))
            self.assertEqual([{'index': 0, '_id': oid}], reply.get('upserted'))

            # update one
            coll.update({'x': 1}, {'$inc': {'x': 1}})
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('update', coll.name),
                            ('updates', [SON([('q', {'x': 1}),
                                              ('u', {'$inc': {'x': 1}}),
                                              ('multi', False),
                                              ('upsert', False)])]),
                            ('ordered', True)])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('update', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(1, reply.get('n'))
            self.assertEqual(1, reply.get('nModified'))

            # update many
            coll.update({'x': 2}, {'$inc': {'x': 1}}, multi=True)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('update', coll.name),
                            ('updates', [SON([('q', {'x': 2}),
                                              ('u', {'$inc': {'x': 1}}),
                                              ('multi', True),
                                              ('upsert', False)])]),
                            ('ordered', True)])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('update', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(1, reply.get('n'))
            self.assertEqual(1, reply.get('nModified'))

            # remove one
            coll.remove({'x': 3}, multi=False)
            results = self.listener.results
            started = results.get('started')
            succeeded = results.get('succeeded')
            self.assertIsNone(results.get('failed'))
            self.assertIsInstance(started, monitoring.CommandStartedEvent)
            expected = SON([('delete', coll.name),
                            ('deletes', [SON([('q', {'x': 3}),
                                              ('limit', 1)])]),
                            ('ordered', True)])
            self.assertEqual(expected, started.command)
            self.assertEqual('pymongo_test', started.database_name)
            self.assertEqual('delete', started.command_name)
            self.assertIsInstance(started.request_id, int)
            self.assertEqual(self.client.address, started.connection_id)
            self.assertIsInstance(succeeded, monitoring.CommandSucceededEvent)
            self.assertIsInstance(succeeded.duration_micros, int)
            self.assertEqual(started.command_name, succeeded.command_name)
            self.assertEqual(started.request_id, succeeded.request_id)
            self.assertEqual(started.connection_id, succeeded.connection_id)
            reply = succeeded.reply
            self.assertEqual(1, reply.get('ok'))
            self.assertEqual(1, reply.get('n'))

            self.assertEqual(0, coll.count())


if __name__ == "__main__":
    unittest.main()
