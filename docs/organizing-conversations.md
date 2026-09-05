# Organizing conversations

**This is organization, not security.** AIDA's user names are labels for
separating conversations on a shared beamline machine, or projects on one
person's laptop. Anyone using the same OS account can pick any name. There is
no password, no permission boundary, and nothing prevents one name from
reading another name's files.

## Pick the active user

In `aida-gui`, use the editable **User** box in the toolbar. Choose a name
used before or type a new one. Changing it starts a new conversation; it
never relabels the conversation already open.

For terminal and unattended use:

```bash
aida chat --user "Jan Ilavsky"
aida run my-workflow --user "Jan Ilavsky"
export AIDA_USER="Jan Ilavsky"
```

You can also set `active_user` in `~/.aida/config.yaml`. Resolution order is
the command-line `--user`, then `$AIDA_USER`, then `active_user`. A blank
value means no user label.

## Find conversations

The sidebar search matches the visible title and workspace as well as the
user label. Once any conversation carries a label, a filter above the list
narrows it to one name.

Selecting a name shows **only** that name's conversations. Conversations
with no label — including all history from before user labels existed — are
reached with the **(no user)** entry, and **All users** always restores the
complete list. The filter follows the toolbar's User box when you switch
users, and otherwise leaves whatever you picked alone.

The CLI equivalent is:

```bash
aida conversations list --user "Jan Ilavsky"
aida conversations list --all-users
```

## Fix a mistake

Nothing validates a typed name, so `Jan`, `jan` and `Jam` are three
different labels. Two repairs, for the two different mistakes:

- **File → Manage Users…** lists every label with its conversation count.
  **Rename or Merge…** moves *all* of one label's conversations to another
  name; if that name already exists the two are merged, which is what
  fixing a typo means. **Clear Label…** removes the label — the
  conversations stay and become visible under every name. There is no
  "delete user": deleting conversations is the sidebar's job.
  **New User…** starts using a name (the same thing as typing one in the
  toolbar).
- **Right-click a conversation → Move to User** moves just the
  conversations you selected. This is the repair for having had the wrong
  name active when a chat was started — the one thing renaming cannot fix,
  since renaming moves everything a label owns. It works on a
  multi-selection, and offers **(no user)** and **New user…** alongside the
  existing names.

Neither changes a conversation's timestamp: relabelling is not activity, so
it will not reorder the list or change what **Clean Up…** would catch.

## Personal context per user

**Settings → Personal context** is the sentence or two the model always
sees. With a user selected the box edits *that user's* text — the label
says whose — and anyone without their own falls back to the text saved
with no user selected. That fallback is deliberate: on a shared machine
most of the useful framing ("this is the USAXS instrument, these are the
detectors") is true for everyone, and only a line or two differs per
person.

Clearing the box removes that user's entry rather than saving an empty
one, so they fall back to the shared text again. In `config.yaml`:

```yaml
user_context: "This is the USAXS instrument at APS 9-ID."
user_contexts:
  "Jan Ilavsky": "Jan runs the beamline and writes the analysis code."
```

## Separate files by user

Write `{user}` in any of these configured paths:

- `AppConfig.records_dir`
- workspace `target_folder`
- workspace `saved_scripts_dir`
- workspace `templates_dir`
- workspace `source_folders`

AIDA replaces it with a path-safe slug before using the path. For example,
`Jan Ilavsky` becomes `jan-ilavsky`; with no active name it becomes
`default`.

```yaml
saved_scripts_dir: "~/Documents/Aida/scripts/{user}"
```

This keeps files tidy, but it is not access control. Anyone with access to
the shared OS account can select another label and browse files permitted to
that account. Use separate OS accounts and filesystem permissions when
confidentiality is required.
