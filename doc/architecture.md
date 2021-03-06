
Catalog server architecture
===========================

The architecture of the catalog is a compromise between something that
can be built reasonably quickly, but still easily add additional
functionality and scaling improvements later.

In development it should be possible to run as a single process, or a
very small number of processes, but in production to be deployed with
processes dedicated to specific tasks or functions.

The catalog should allow scaling to handle larger loads, either via
parallel identical instances or by sharding.

Functions that cannot be parallelised should enable reliability by
switching from a master to a slave instance, if they are critical to
the operation of the system.


Overview
--------

[Architecture diagram](architecture.png)

A frontend handles all requests from clients or third-party users,
relying on a set of modules to handle each query.

There can be more than one frontend, sharing some common library code
(e.g. the implementation of REST endpoint handlers) but each having
its own routing and thus its own functionality.

The catalog modules typically consist of:

* An interface for the frontends to the stored data, containing any
  business logic

* A data model, stored as a set of collections in a MongoDB database

* Backend tasks that perform data updates or other actions based on
  events triggered from the frontend or other backend tasks.

Each module can be tested in isolation.

The backend tasks should focus on the business logic, relying on
common functionality in backend libraries and the event infrastructure.

An event transfer component is responsible for persisting events from
the modules into a long-term event storage, as well as for sending
them to the message queues that the backend tasks listen to.


Data
----

The catalog is centered around a core module whose data model is
stored in a MongoDB database.  These objects are created and updated
by users of the catalog, and also are queried for basic access.

A CQRS architecture allow more complex functionality to be handled by
separate modules supporting specific read patterns.  This includes:

* Search: Full-text and URI searches
* View: Denormalisations to speed up web views of the data

A later module is expected to handle correlations and aggregation of
metadata between works.


Events
------

All commands in the core module generates events, which background
tasks in the other modules can listen to update their own data models.

Event sourcing is not used now, but the design is intended to enable
the core module to be rewritten later to use it.  For now, the update
of the core objects in the database is the primary action, event
emitting secondary.

The events also serve the purpose of an audit log.


Current status
==============

The work on the catalog is for the moment focused on just implementing
the index frontend and the functions necessary to support This.

Core
----

Works, media, users, and orgs are implemented, groups and collections
are missing.

All changes generate the basic events.  Mirror events are now
generated by a backend process (and thus depends on the limited event
support), but this should be changed to be a part of the normal
update.

Search
------

URI and blockhash lookup is implemented.  Text search is not done.

The search MongoDB database is populated by the load script for core.
The blockhash DB is populated by a separate load script.

In a full system it should instead consume events to both populate and
update the search DB in tandem with changes to the core.


Event
------

A proof-of-concept event implementation that transfers events from the
temporary capped collections into the long-term storage in the event
database exist.  However, there are no checks that this is running or
keeping up with what happens in the core database.  Events will thus
be lost if the head of the capped collection catches up with the last
event that has been transferred into the long-term collection.

This can be handled with a pretty simple implementation that
periodically checks that all is OK:
https://github.com/commonsmachinery/catalog/issues/72

The only mechanism for getting events to backend tasks is now zeromq
pub/sub. These are not guaranteed delivery, so a more robust queueing
will be needed.  See the detailed description of the event module below.


Catalog frontend
----------------

Work on this frontend has been paused.  Some functionality for works
is implemented, but the interface is very rudimentary.

The session handling needs change:
https://github.com/commonsmachinery/catalog/issues/71

The login mechanism should also change to use the Auth module (when
that is implemented).


Other missing pieces
--------------------

Nothing has been done on admin frontend, auth, audit, or view.


Server components
=================

Frontends
---------

There are several server frontends providing different web and REST
interfaces to the catalog.

They share some common functionality, such as web page view mixins and
REST endpoint implementations.  The routing that collects the
functionality into a single HTTPS endpoint, and thus controls what can
be done through that endpoint, is unique to each frontend.

In a typical deployment the admin interface would be deployed on
different servers to the main catalog endpoint.  The index-only
endpoint can be omitted, or in larger setups a load balancing proxy
layer can route public search requests to dedicated index endpoint
servers while sending the rest of the catalog request to the main
frontend servers.

Since the frontends are responsible for facing the world, they are
also responsible for creating the URIs that identify the resources
managed by the catalog.  These URIs include the MongoDB ObjectIDs that
identify the objects in the main database.  They also translate from
the internal object representation into an extended structure that
better supports web apps.


### Catalog frontend

The main catalog frontend provide access over web pages and a REST API
to the full functionality of the catalog.  This includes both
anonymous browsing of the public catalog content as well as allowing
access to private content and modification of it after logging in.

The web pages and the REST API are overlaid on the same URIs, using
the HTTP Accept header to determine what to respond.

A typical request would first check with the auth module that the user
isn't locked and find out what groups the user belongs to.  The core
module is then called, with this information, to perform any updates.
To generate a response, data from the core module is used, potentially
helped by the view and search modules.


### Index frontend

This frontend only supports looking up public resources based on URIs
or hashes, redirecting to where further information can be found about
the resource.

This will be the first frontend to be built to support the initial
elog.io deployment, and as such it will also incorporate a few web
pages to present resource information.  They should later be replaced
by simple redirects to the catalog frontend, allowing the index to be
completely separated from the catalog and potentially spanning
multiple catalogs or other sites.


### Admin frontend

The admin interface is a separate small web app, which allows site
administrators to manage users and get access to the catalog data in a
controlled manner.


Core
----

The core module defines the central data model of the catalog, and is
responsible for implementing the business logic related to accessing
and updating the objects.  All data modifications result in a set of
events being generated.

Backend tasks mainly perform database maintenance, verifying integrity
and garbage collecting superfluous objects.


Auth
----

The auth module handles all aspects of user authentication and
authorization to access the system as a whole.  Authorization to
access specific data objects are handled by the core module, based on
identity information provided by the auth module via the frontend.

The auth module generates security-related events, such as logins,
failed accesses, account locks etc.

Backend tasks consume these events to e.g. lock accounts after
repeatedly failed accesses.


Audit
-----

The event log also functions as an audit log on all changes made to
data in the system and other security events.  The audit module
provide access to these logs for the frontends.


View
----

The view module provides denormalisations of the core data module to
help the frontend render web page responses.  It also contain
information only relevant to web pages, e.g. view counts.

It will initially do very little in the beginning of the project.  The
frontend will instead do the legwork in assembling the information
from the core module, which can later be taken over by the view module
as needed.

Backend tasks will update the view denormalisations based on core
events.


Search
------

The search module supports locating objects based on URIs or full-text
searches, relying on MongoDB indexes and a HmSearch hash lookup
database.

Most of the work will be done by background tasks that updates the
search database based on core events.


Event
-----

The event module handles the details around event processing to let
the other modules easily generate and consume events.

### Event generation

Events are generated either as part of a command execution (with
`command.execute`) or as standalone events (with `command.logEvent`).
Publishing these events is a three-step process.

These events are first written to a capped collection with no indices
in the same database as the generating module
(e.g. `core-dev/coreevents` for the core module).  This means that it
is very unlikely that writing the events should fail if the
immediately preceding object write succeeded.  This level of
reliability should be sufficient for this application.  If a write
anyway fails, the events are logged to a file instead and an alarm
raised.  This should trigger a lock on the entire system to prevent
further modifications to the module data.

### Transfer and logging

A backend event transfer process is responsible for following these
capped collections with MongoDB tailable cursors, copying the event
batches into an event log collection.  If this job should stop or not
catch up, a watchdog function will stop further data modifications
until the job is up to speed again.

The event log is considered an archival log, partitioned on years or
even months to avoid building up too big collections.

### Event consumption by backend tasks

After an event batch is written to the collection, each event in the
batch must reach the backend tasks that care about that type of
events.

Two implementation alternatives should be considered.  The first one
is probably much quicker to get in place and will suffice for lower
event loads, but as the system scales it may be needed with a propery
message bus.

#### Polling (with pub/sub as bonus)

In this implementation backend tasks recieve events primarily by
periodically polling the event log, asking for any new events matching
some criteria.  This could be done once a minute.  Some support for
this already exist in the event module.

To reduce the event processing delay the backend tasks can also
subscribe to events on a ZeroMQ PUB socket.  The event transfer task
will send all events over this socket once they have been stored in
the event log.

Backend tasks that can live with missing some events can rely solely
on the ZeroMQ socket.  (This is already implemented.)

Backend tasks that must process all events should only use this socket
to trigger an early check in the event log.  (They must still do some
rate limiting e.g. not checking more often than every 5s or 10s.)

While the basic support for this is in place, some scaffolding would
be useful so that the backend tasks doesn't have to implement all this
themselves, as well as handling failover to a slave task if the master
task fails.


#### Message bus

Using a message bus removes (much of) the need to poll the event log,
and makes the event processing much quicker.  However, it adds
complexity as another infrastructure component is needed.

RabbitMQ is the likeliest candidate here.  It has support for
persistent queues, event acknowledgement, and synchronisation between
multiple queue clients.  This means that we could mostly rely on the
AMQP protocol and RabbitMQ to ensure that no events are dropped and
to implement synchronisation between master and slave processes for
backend tasks.

While the event processing is important to the operation of the
catalog, it is sufficient that it can be restarted after any failure
to ensure that all events are processed eventually, but not
necessarily immediately.  In the meantime, functions such as search
might be degraded by not returning fully up-to-date results.

Thus, it would suffice with a single RabbitMQ instance, avoiding the
complexity of mirrored setups, instead putting effort into tools to
restart the event processing in case of failure.  Similarly while
persistent queues will be used, loss of the underlying storage can
also be handled by the restart tools after getting a new system up and
running.  This will avoid most of the operational complexities of
running a high-reliability event broker.

Each task will have its own queue on a topic exchange defining the
event subscriptions that are relevant to the task.  Typically more
than one instance of the task would be run, using different event
acknowledgement and exclusitivity models depending on if parallel
event processing is possible or not for the task logic.


Technology
==========

The catalog should be possible to deploy in Docker environments or on
a Platform-as-a-Service (PaaS).

Frontend
--------

* Node
* Express 4 web app
* Jade templates with Stylus CSS processing
* Backbone.js to create/update works etc on the web pages


Backend tasks
-------------

Primarily Node, to be able to share libraries and data models with the
modules used by the frontend.  A plain AMQP library is probably
sufficient to drive the tasks, but some more sophisticated
event-driven task library might be involved later.


Databases
---------

MongoDB is a widely used document store, which is a very good fit to
Node.  Great support in PaaS services, and can handle very large
datasets.


Messaging
---------

TBD.  If an external message bus is used, RabbitMQ in single-node mode
without any high-reliability requirements is the likely choice.


Caching
-------

Any in-memory caching should primarily be within the processes, with
the help of some cluster cache library to evict data on changes.  To
be investigated when the need arises.

It is not expected that cluster-wide caching will be needed, but if it
is a memory-only Redis server would be a good solution.

