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
user label. When labelled conversations exist, the **All users** selector
can narrow the list to one name. Unlabelled conversations—including history
created before user labels existed—remain visible under every selection.
Switch back to **All users** to restore the complete list.

The CLI equivalent is:

```bash
aida conversations list --user "Jan Ilavsky"
aida conversations list --all-users
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
