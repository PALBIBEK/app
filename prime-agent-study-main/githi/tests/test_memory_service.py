"""One facade over storage + index + search.

The memory module is ~1,500 lines across 14 files, each built against its own
tests and never against the next one. The seams show: `MemoryIndex` takes a db
path rather than the storage it indexes, the method is `index_file(path,
content, source)`, and `SearchResult` carries `snippet` rather than `text`. It
took four attempts to write a working script by hand. A caller should not have
to know any of that, and the agent certainly should not.

So the surface is two verbs -- `recall` and `remember` -- and everything else
is internal.

Two properties matter more than the API shape:

**Appending, never overwriting.** `MEMORY.md` is the only copy of what was
learned. A `remember` that rewrites the file loses every prior fact, and
nothing would report the loss.

**Precision over recall.** Injected memory costs tokens on every user turn.
A search that returns its best guess for *any* query adds noise to every
prompt and teaches the model to ignore the section -- so an unrelated query
must come back empty rather than "closest available".
"""

from __future__ import annotations

import pytest

from githi.memory.service import MemoryService


@pytest.fixture
def service(tmp_path):
    return MemoryService(root=tmp_path)


@pytest.fixture
def stocked(service):
    service.remember("GitHub's search/issues endpoint counts pull requests as "
                     "issues. Use `type:issue` to exclude them.")
    service.remember("Unauthenticated GitHub API allows 60 requests per hour; "
                     "search endpoints allow 10 per minute.")
    return service


class TestRememberAndRecall:
    def test_a_remembered_fact_is_recalled(self, stocked):
        hits = stocked.recall("how do I count open issues without pull requests")
        assert any("type:issue" in h for h in hits)

    def test_recall_on_empty_memory_is_empty(self, service):
        assert service.recall("anything at all") == []

    def test_remember_appends_rather_than_overwrites(self, stocked):
        """`MEMORY.md` is the only copy. A rewrite silently discards every
        earlier fact."""
        stocked.remember("A third fact about pagination.")
        text = stocked.read()
        assert "type:issue" in text and "60 requests" in text and "pagination" in text

    def test_the_limit_is_respected(self, stocked):
        for i in range(10):
            stocked.remember(f"Fact number {i} about github repositories.")
        assert len(stocked.recall("github", limit=3)) <= 3

    def test_an_unrelated_query_recalls_nothing(self, stocked):
        """Precision matters more than recall here: injected memory costs
        tokens on every user turn, and a store that answers every query
        teaches the model to ignore the section entirely."""
        assert stocked.recall("what is the capital of France") == []

    def test_remembering_the_same_fact_twice_does_not_duplicate_it(self, stocked):
        fact = "GitHub's search/issues endpoint counts pull requests as issues."
        before = stocked.read().count(fact)
        stocked.remember(fact)
        assert stocked.read().count(fact) == before


class TestPersistence:
    def test_a_new_service_sees_earlier_facts(self, tmp_path):
        """The whole point. A fact learned in one session must survive into
        the next process."""
        MemoryService(root=tmp_path).remember(
            "The langchain repo tags releases per-package, like langchain-core==1.5.3.")
        hits = MemoryService(root=tmp_path).recall("how are langchain releases tagged")
        assert any("per-package" in h for h in hits)

    def test_a_deleted_index_is_rebuilt_from_markdown(self, tmp_path):
        """The index is a disposable cache; the markdown is the truth. Deleting
        the database must cost time, never knowledge."""
        service = MemoryService(root=tmp_path)
        service.remember("Flask is maintained by the Pallets organisation.")
        service.index_path.unlink()

        hits = MemoryService(root=tmp_path).recall("who maintains flask")
        assert any("Pallets" in h for h in hits)

    def test_hand_edited_markdown_is_picked_up(self, tmp_path):
        """Markdown-as-truth means a text editor is a valid way to manage
        memory -- so an edit made outside githi has to be noticed."""
        service = MemoryService(root=tmp_path)
        service.remember("first fact about caching")
        service.memory_path.write_text(
            service.read() + "\n- Ripgrep is faster than grep for large trees.\n",
            encoding="utf-8")

        hits = MemoryService(root=tmp_path).recall("what is faster than grep")
        assert any("Ripgrep" in h for h in hits)


class TestResilience:
    """Memory is an enhancement. It must never be the reason a run fails."""

    def test_a_corrupt_index_does_not_raise(self, tmp_path):
        service = MemoryService(root=tmp_path)
        service.remember("a fact")
        service.index_path.write_bytes(b"this is not a sqlite database")
        assert MemoryService(root=tmp_path).recall("a fact") is not None

    def test_recall_never_raises(self, tmp_path):
        """Whatever the state of the store, a failed recall returns nothing
        and the turn proceeds."""
        service = MemoryService(root=tmp_path)
        assert service.recall("") == []
        assert service.recall("   ") == []

    def test_an_unwritable_root_does_not_raise(self, tmp_path):
        service = MemoryService(root=tmp_path / "nested" / "deep")
        service.remember("still works")           # must create the tree
        assert service.recall("still works")

    def test_remember_ignores_empty_input(self, service):
        service.remember("")
        service.remember("   ")
        assert service.read().strip() == "" or "\n" in service.read()


class TestFormatting:
    def test_recalled_text_is_usable_as_a_prompt_block(self, stocked):
        """It goes straight into a message, so it has to be readable text --
        not a repr, not JSON."""
        block = stocked.recall_block("counting open issues")
        assert "type:issue" in block and "SearchResult" not in block

    def test_the_block_is_empty_when_nothing_matches(self, stocked):
        """An empty string, not a header with nothing under it -- a caller
        appends it unconditionally and must stay silent when there is
        nothing to say."""
        assert stocked.recall_block("the capital of France") == ""

    def test_the_block_says_where_it_came_from(self, stocked):
        """The model must be able to tell recalled context from what the user
        actually said, or it will attribute a remembered fact to them."""
        block = stocked.recall_block("counting open issues")
        assert "memory" in block.lower()
