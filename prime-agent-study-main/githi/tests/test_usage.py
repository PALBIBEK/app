"""Token and cost accounting.

Cost is the thing this harness is being optimised for, so it has to be
*measured*, not estimated after the fact. Cache tokens are tracked separately
because they bill at roughly a tenth of fresh input -- folding them together
would hide whether caching is working at all.
"""

from __future__ import annotations

from githi.llm.usage import ModelPricing, UsageTracker, parse_usage
from githi.types import Usage


class TestParseUsage:
    def test_reads_the_standard_fields(self):
        u = parse_usage({"prompt_tokens": 100, "completion_tokens": 20})
        assert (u.input_tokens, u.output_tokens) == (100, 20)

    def test_reads_openai_style_cached_tokens(self):
        u = parse_usage({"prompt_tokens": 100, "completion_tokens": 5,
                         "prompt_tokens_details": {"cached_tokens": 80}})
        assert u.cache_read_tokens == 80

    def test_cached_tokens_are_not_double_counted(self):
        """OpenAI reports cached tokens *inside* prompt_tokens. Adding them
        again would overstate both token count and cost."""
        u = parse_usage({"prompt_tokens": 100, "completion_tokens": 5,
                         "prompt_tokens_details": {"cached_tokens": 80}})
        assert u.input_tokens == 20

    def test_reads_anthropic_style_cache_fields(self):
        u = parse_usage({"prompt_tokens": 10, "completion_tokens": 5,
                         "cache_read_input_tokens": 900,
                         "cache_creation_input_tokens": 50})
        assert u.cache_read_tokens == 900 and u.cache_write_tokens == 50

    def test_missing_usage_is_zero_not_a_crash(self):
        """Some gateways omit usage entirely; that must not end a run."""
        assert parse_usage(None).total_tokens == 0
        assert parse_usage({}).total_tokens == 0


class TestPricing:
    def test_cost_of_plain_tokens(self):
        p = ModelPricing(input_per_m=1.0, output_per_m=5.0)
        cost = p.cost(Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        assert abs(cost - 6.0) < 1e-9

    def test_cache_reads_are_much_cheaper(self):
        p = ModelPricing(input_per_m=1.0, output_per_m=5.0, cache_read_per_m=0.1)
        cached = p.cost(Usage(cache_read_tokens=1_000_000))
        fresh = p.cost(Usage(input_tokens=1_000_000))
        assert cached < fresh / 5

    def test_cache_writes_cost_a_premium(self):
        """Writes bill above normal input -- which is why marking a tiny
        prefix as cacheable is a loss, not a saving."""
        p = ModelPricing(input_per_m=1.0, output_per_m=5.0, cache_write_per_m=1.25)
        assert p.cost(Usage(cache_write_tokens=1_000_000)) > p.cost(Usage(input_tokens=1_000_000))

    def test_unknown_model_reports_no_cost_rather_than_guessing(self):
        assert ModelPricing.for_model("some/unknown-model") is None


class TestTracker:
    def test_accumulates_across_turns(self):
        t = UsageTracker()
        t.add(Usage(input_tokens=10, output_tokens=2))
        t.add(Usage(input_tokens=5, output_tokens=1))
        assert t.total.input_tokens == 15 and t.total.output_tokens == 3

    def test_counts_turns(self):
        t = UsageTracker()
        t.add(Usage())
        t.add(Usage())
        assert t.turns == 2

    def test_reports_cache_hit_rate(self):
        """The one number that says whether caching is actually working."""
        t = UsageTracker()
        t.add(Usage(input_tokens=100, cache_read_tokens=900))
        assert 0.89 < t.cache_hit_rate < 0.91

    def test_cache_hit_rate_is_zero_when_nothing_sent(self):
        assert UsageTracker().cache_hit_rate == 0.0

    def test_summary_mentions_turns_and_tokens(self):
        t = UsageTracker()
        t.add(Usage(input_tokens=10, output_tokens=2))
        summary = t.summary("anthropic/claude-haiku-4.5")
        assert "1" in summary and "token" in summary.lower()
