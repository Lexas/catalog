
Catalog data models
===================

The catalog consists of a set of logically separate modules, where
most of them have a data model of their own.  The non-core data models
typically refer to objects in the core data model.

* Core: Facts about organisations, users, groups, works, media and
  collections.

* Event: Log all events in the system.

* Auth: Authentication and authorization to access the catalog.

* View: Thumbnails, view counts, object summaries and other data which
  improves the presentation of the core data.

* Search: Locate works based on URIs or full-text searches.


Documentation
-------------

The data models are documented in UML diagrams drawn with
[UMLet](http://umlet.com/) and exported as PNGs.

Data model semantics are described in corresponding markdown
documents.
