We will create an isolated home directory for our db.

  $ rm -rf /tmp/dirdiff-cram-mark
  $ mkdir -p /tmp/dirdiff-cram-mark/home
  $ export HOME=/tmp/dirdiff-cram-mark/home

And three test directories.
  $ mkdir -p /tmp/dirdiff-cram-mark/repo
  $ mkdir /tmp/dirdiff-cram-mark/other
  $ mkdir /tmp/dirdiff-cram-mark/superabsolute

Now let us go there.
  $ cd /tmp/dirdiff-cram-mark

List an empty registry.

  $ dirdiff mark --list
  No marked repos.

Mark the current directory. It will use your current directory by default.

  $ cd repo
  $ dirdiff mark
  Marked repo 1: repo

List the registry after the first mark. The stored path is absolute.

  $ dirdiff mark --list
  1. repo
     path: /tmp/dirdiff-cram-mark/repo

You can also pass explicit name.

  $ cd ../other
  $ dirdiff mark --name "work repo"
  Marked repo 2: work repo

List multiple marks. Marks are ordered newest first.

  $ dirdiff mark --list
  2. work repo
     path: /tmp/dirdiff-cram-mark/other
  1. repo
     path: /tmp/dirdiff-cram-mark/repo

And of course you can pass an explicit path
  $ dirdiff mark --path /tmp/dirdiff-cram-mark/superabsolute
  Marked repo 3: superabsolute

Let us show them.
  $ dirdiff mark --list
  3. superabsolute
     path: /tmp/dirdiff-cram-mark/superabsolute
  2. work repo
     path: /tmp/dirdiff-cram-mark/other
  1. repo
     path: /tmp/dirdiff-cram-mark/repo

Let's remove it all at the end.
  $ rm -rf /tmp/dirdiff-cram-mark/home
