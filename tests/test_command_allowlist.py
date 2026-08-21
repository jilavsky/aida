from __future__ import annotations

from aida.workspace.command_allowlist import CommandAllowlist


def test_empty_allowlist_allows_nothing():
    allowlist = CommandAllowlist.empty()
    assert not allowlist.is_allowed("git status")


def test_exact_match_is_allowed():
    allowlist = CommandAllowlist(patterns=["git status", "ls"])
    assert allowlist.is_allowed("git status")
    assert allowlist.is_allowed("ls")


def test_exact_match_with_extra_args_is_not_allowed():
    allowlist = CommandAllowlist(patterns=["git status"])
    assert not allowlist.is_allowed("git status --short")


def test_trailing_wildcard_matches_any_further_arguments():
    allowlist = CommandAllowlist(patterns=["git log *"])
    assert allowlist.is_allowed("git log")
    assert allowlist.is_allowed("git log --oneline -5")
    assert allowlist.is_allowed("git log HEAD~3..HEAD")


def test_trailing_wildcard_does_not_match_a_different_leading_command():
    allowlist = CommandAllowlist(patterns=["git log *"])
    assert not allowlist.is_allowed("git status")
    assert not allowlist.is_allowed("git")


def test_unmatched_command_is_not_allowed():
    allowlist = CommandAllowlist(patterns=["git status", "ls"])
    assert not allowlist.is_allowed("rm -rf /")


def test_quoted_arguments_tokenize_like_a_real_shell():
    allowlist = CommandAllowlist(patterns=["echo *"])
    assert allowlist.is_allowed('echo "hello world"')


def test_unbalanced_quotes_are_not_allowed_not_an_error():
    allowlist = CommandAllowlist(patterns=["git status"])
    assert not allowlist.is_allowed('git "status')


def test_blank_command_is_not_allowed():
    allowlist = CommandAllowlist(patterns=["git status"])
    assert not allowlist.is_allowed("")
    assert not allowlist.is_allowed("   ")


def test_blank_pattern_in_the_list_is_ignored():
    allowlist = CommandAllowlist(patterns=["", "git status"])
    assert allowlist.is_allowed("git status")
    assert not allowlist.is_allowed("")
