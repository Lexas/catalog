# -*- coding: utf-8 -*-
#
# backend - query/update graphs for the Commons Machinery metadata catalog
#
# Copyright 2014 Commons Machinery http://commonsmachinery.se/
#
# Distributed under an AGPLv3 license, please see LICENSE in the top dir.

from celery import Celery
from celery import Task
from celery import subtask
from celery.signals import worker_shutdown
from celery.signals import task_success
from catalog.store import RedlandStore, EntryNotFound
from catalog.log import MongoDBLog
import os, time
import errno
import json

app = Celery('catalog_backend', broker='amqp://guest@localhost:5672//')

app.conf.update(
    CELERY_TASK_SERIALIZER='json',
    CELERY_ACCEPT_CONTENT = ['json'],
    CELERY_RESULT_SERIALIZER='json',
    CELERY_RESULT_BACKEND = 'amqp',
    CELERY_TASK_RESULT_EXPIRES = 30,
    CELERY_TASK_RESULT_DURABLE = False,
    CELERY_IMPORTS = ("catalog.store", "catalog.log", "catalog_backend"),
)

class FileLock(object):
    def __init__(self, id, timeout=15, lockdir = '.'):
        self._filename = os.path.join(lockdir, 'lock-%s' % id)
        self._timeout = timeout
        self._locked = False

    def __enter__(self):
        assert not self._locked

        pid = str(os.getpid())
        timeout = self._timeout

        # Attempt locking by creating a symlink.  This is an atomic
        # operation that will succeed only if the link doesn't already
        # exist.  Use the PID as the "target file" to provide info
        # about who holds the link

        while True:
            try:
                os.symlink(pid, self._filename)
            except OSError, e:
                if e.errno == errno.EEXIST:
                    if timeout > 0:
                        time.sleep(1)
                        timeout -= 1
                    else:
                        raise RuntimeError("Timeout error while trying to lock access to work")
                else:
                    raise
            else:
                self._locked = True
                return


    def __exit__(self, *args):
        if self._locked:
            try:
                os.remove(self._filename)
            except OSError, e:
                if e.errno == errno.ENOENT:
                    print('warning: lock file unexpectedly removed')
                else:
                    raise
            self._locked = False


class StoreTask(app.Task):
    abstract = True
    _main_store = None
    _public_store = None
    _log = None

    @property
    def main_store(self):
        if self._main_store is None:
            self._main_store = RedlandStore("works")
        return self._main_store

    @property
    def public_store(self):
        if self._public_store is None:
            self._public_store = RedlandStore("public")
        return self._public_store

    @property
    def log(self):
        if self._log is None:
            self._log = MongoDBLog()
        return self._log

@app.task(base=StoreTask, bind=True)
def create_work(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    work = store.store_work(**kwargs)

    time = kwargs['timestamp']
    user = kwargs['user']
    resource = work['resource']
    #payload = json.dumps(kwargs)
    payload = json.dumps(work.get_data())

    log_event.apply_async(args=('create_work', time, user, resource, payload))
    if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

    return work.get_data()

@app.task(base=StoreTask, bind=True)
def update_work(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    id = kwargs['id']

    with FileLock(id):
        work = store.update_work(**kwargs)

        time = kwargs['time'] # as in rest.js
        user = kwargs['user']
        resource = work['resource']
        payload = json.dumps(work.get_data())

        log_event.apply_async(args=('update_work', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        return work.get_data()

@app.task(base=StoreTask, bind=True)
def delete_work(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    id = kwargs['id']

    with FileLock(id):
        # here we temporarily get the resource to properly
        # log delete events. TODO: update this when accessing works by URL is implemented
        work = store.get_work(user=kwargs['user'], id=kwargs['id'])

        time = kwargs['timestamp']
        user = kwargs['user']
        resource = work['resource']
        payload = None

        log_event.apply_async(args=('delete_work', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        return kwargs

@app.task(base=StoreTask, bind=True)
def get_work(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.get_work(**kwargs)

@app.task(base=StoreTask, bind=True)
def get_works(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.query_works_simple(**kwargs)

# sources of works

@app.task(base=StoreTask, bind=True)
def add_source(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    work_id = kwargs.get('work_id', None)

    if work_id:
        with FileLock(work_id):
            source = store.store_source(**kwargs)
            # here we temporarily get the resource to properly
            # log delete events. TODO: update this when accessing works by URL is implemented
            work = store.get_work(user=kwargs['user'], id=kwargs['work_id'])

            time = kwargs['timestamp']
            user = kwargs['user']
            resource = work['resource']
            payload = json.dumps(source.get_data())

            log_event.apply_async(args=('add_source', time, user, resource, payload))
            if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

            return source.get_data()
    else:
        source = store.store_source(**kwargs)

        time = kwargs['timestamp']
        user = kwargs['user']
        resource = None
        payload = json.dumps(source.get_data())

        log_event.apply_async(args=('add_source', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        return source.get_data()

@app.task(base=StoreTask, bind=True)
def get_source(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.get_source(**kwargs)

@app.task(base=StoreTask, bind=True)
def get_sources(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.get_sources(**kwargs)

@app.task(base=StoreTask, bind=True)
def update_source(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    work_id = kwargs.get('work_id', None)

    if work_id:
        with FileLock(work_id):
            source = store.update_source(**kwargs)
            # here we temporarily get the resource to properly
            # log delete events. TODO: update this when accessing works by URL is implemented
            work = store.get_work(user=kwargs['user'], id=kwargs['work_id'])

            time = kwargs['timestamp']
            user = kwargs['user']
            resource = work['resource']
            payload = json.dumps(source.get_data())

            log_event.apply_async(args=('update_source', time, user, resource, payload))
            if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

            return source.get_data()
    else:
        source = store.update_source(**kwargs)

        time = kwargs['timestamp']
        user = kwargs['user']
        resource = None
        payload = json.dumps(source.get_data())

        log_event.apply_async(args=('update_source', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        return source.get_data()

    return store.update_source(**kwargs)

@app.task(base=StoreTask, bind=True)
def delete_source(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    work_id = kwargs.get('work_id', None)

    if work_id:
        with FileLock(work_id):
            store.delete_source(**kwargs)

            # here we temporarily get the resource to properly
            # log delete events. TODO: update this when accessing works by URL is implemented
            work = store.get_work(user=kwargs['user'], id=kwargs['work_id'])

            time = kwargs['timestamp']
            user = kwargs['user']
            resource = work['resource']
            payload = json.dumps(kwargs)

            log_event.apply_async(args=('delete_source', time, user, resource, payload))
            if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

            return kwargs
    else:
        store.delete_source(**kwargs)

        time = kwargs['timestamp']
        user = kwargs['user']
        resource = None
        payload = json.dumps(kwargs)

        log_event.apply_async(args=('delete_source', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        return kwargs

# posted instances

@app.task(base=StoreTask, bind=True)
def add_post(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    work_id = kwargs.get('work_id', None)

    with FileLock(work_id):
        post = store.store_post(**kwargs)

        # here we temporarily get the resource to properly
        # log events. TODO: update this when accessing works by URL is implemented
        work = store.get_work(user=kwargs['user'], id=work_id)

        time = kwargs['timestamp']
        user = kwargs['user']
        resource = work['resource']
        payload = json.dumps(post.get_data())

        log_event.apply_async(args=('add_post', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        return post.get_data()

@app.task(base=StoreTask, bind=True)
def get_posts(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.get_posts(**kwargs)

@app.task(base=StoreTask, bind=True)
def get_post(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.get_post(**kwargs)

@app.task(base=StoreTask, bind=True)
def delete_post(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    work_id = kwargs.get('work_id', None)

    with FileLock(work_id):
        # here we temporarily get the resource to properly
        # log events. TODO: update this when accessing works by URL is implemented
        work = store.get_work(user=kwargs['user'], id=work_id)

        time = kwargs['timestamp']
        user = kwargs['user']
        resource = work['resource']
        payload = None

        log_event.apply_async(args=('delete_post', time, user, resource, payload))
        if store != self.public_store: on_work_updated.apply_async(args=(self.subtask(kwargs=kwargs), ))

        store.delete_post(**kwargs)
        return kwargs

@app.task(base=StoreTask, bind=True)
def get_complete_metadata(self, store='main', **kwargs):
    store = self.main_store if store == 'main' else self.public_store
    return store.get_complete_metadata(**kwargs)

@app.task(base=StoreTask, bind=True)
def log_event(self, type, time, user, resource, data):
    self.log.log_event(type, time, user, resource, data)

@app.task(base=StoreTask, bind=True)
def query_events(self, type=None, user=None, time_min=None, time_max=None, resource=None, limit=100, offset=0):
    return self.log.query_events(type, user, time_min, time_max, resource, limit, offset)

@app.task(base=StoreTask, bind=True)
def on_work_updated(self, update_subtask):
    task = update_subtask['task']
    kwargs = update_subtask['kwargs']

    if task == "catalog_backend.create_work":
        visibility = kwargs.get('visibility', None)
        if visibility != "public":
            return False
    elif task == "catalog_backend.update_work":
        work_id = kwargs['id']
        work = self.main_store.get_work(user=kwargs['user'], id=work_id)
        visibility = kwargs.get('visibility', work['visibility'])
        if visibility != "public":
            return False
    elif task == "catalog_backend.add_source" or \
            task == "catalog_backend.update_source" or \
            task == "catalog_backend.add_post":
        work_id = kwargs['work_id']
        work = self.main_store.get_work(user=kwargs['user'], id=work_id)
        if work['visibility'] != 'public':
            return False

    # work is public or deleted, ok to re-run the updater task for public store now
    sub = subtask(update_subtask)
    sub.apply_async(kwargs={"store": "public"})
