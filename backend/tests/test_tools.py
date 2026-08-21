"""Tests unitaires des fonctions métier — aucun réseau, aucune simulation.

Les fonctions métier (`measure_document`, `find_security_indicators`,
`estimate_analysis_cost`) sont pures et ne sont JAMAIS simulées ici : elles
sont testées avec leur vrai code. C'est MiniMax qui est simulé dans les
tests réseau (`test_agent.py`, `test_api.py`).
"""

import pytest

from backend.app import tools


class TestMeasureDocument:
    def test_exact_counts(self) -> None:
        text = "abc def ghi\n"
        metrics = tools.measure_document(text)
        assert metrics == {
            "character_count": 12,
            "word_count": 3,
            "line_count": 1,
            "estimated_input_tokens": 3,  # round(12 / 4)
        }

    def test_multiline_counts(self) -> None:
        text = "ligne un\nligne deux\nligne trois\n"
        metrics = tools.measure_document(text)
        assert metrics["character_count"] == 32
        assert metrics["word_count"] == 6
        assert metrics["line_count"] == 3

    def test_empty_document_rejected(self) -> None:
        with pytest.raises(tools.InvalidDocumentError):
            tools.measure_document("")

    def test_blank_document_rejected(self) -> None:
        with pytest.raises(tools.InvalidDocumentError):
            tools.measure_document("   \t  \n")

    def test_too_long_document_rejected(self) -> None:
        with pytest.raises(tools.InvalidDocumentError):
            tools.measure_document("a" * (tools.MAX_DOCUMENT_LENGTH + 1))


class TestFindSecurityIndicators:
    def test_no_indicator_returns_empty_list(self) -> None:
        text = "Un texte sans aucun indice de sécurité détectable."
        assert tools.find_security_indicators(text) == []

    def test_empty_document_returns_empty_list(self) -> None:
        assert tools.find_security_indicators("") == []

    def test_multiple_categories_and_line_numbers(self) -> None:
        text = "user=alice\npassword=topSecret123\ncontact=bob@example.com"
        findings = tools.find_security_indicators(text)
        by_name = {finding["pattern_name"]: finding for finding in findings}
        assert "password_assignment" in by_name
        assert "email_literal" in by_name
        assert by_name["password_assignment"]["category"] == "secret"
        assert by_name["password_assignment"]["line_number"] == 2
        assert by_name["email_literal"]["category"] == "privacy"
        assert by_name["email_literal"]["line_number"] == 3

    def test_matched_text_is_masked_and_bounded(self) -> None:
        secret = "password=thisIsMySecretValue12345"
        text = f"user=alice\n{secret}"
        findings = tools.find_security_indicators(text)
        password_findings = [
            finding
            for finding in findings
            if finding["pattern_name"] == "password_assignment"
        ]
        assert password_findings
        masked = password_findings[0]["matched_text"]
        assert secret not in masked
        assert masked.startswith("pa")
        assert masked.endswith("45")
        assert len(masked) <= tools.MAX_MATCHED_TEXT_LENGTH

    def test_findings_are_capped_at_max(self) -> None:
        text = "\n".join(f"password=value{i}" for i in range(20))
        findings = tools.find_security_indicators(text)
        assert len(findings) == tools.MAX_FINDINGS

    def test_findings_are_sorted_by_line_then_name(self) -> None:
        text = "grant all privileges\npassword=topSecret123\neval(1)"
        findings = tools.find_security_indicators(text)
        line_numbers = [finding["line_number"] for finding in findings]
        assert line_numbers == sorted(line_numbers)

    def test_invalid_pattern_rejected_before_analysis(self) -> None:
        bad_pattern = {"name": "broken", "literal_or_regex": "re:(unclosed", "category": "secret"}
        with pytest.raises(tools.InvalidPatternError):
            tools.find_security_indicators("anything", patterns=(bad_pattern,))

    def test_unknown_category_rejected(self) -> None:
        bad_pattern = {"name": "x", "literal_or_regex": "abc", "category": "nope"}
        with pytest.raises(tools.InvalidPatternError):
            tools.find_security_indicators("abc", patterns=(bad_pattern,))

    def test_literal_pattern_matches_substring(self) -> None:
        pattern = {"name": "marker", "literal_or_regex": "SECRET_MARKER", "category": "secret"}
        findings = tools.find_security_indicators("a SECRET_MARKER here", patterns=(pattern,))
        assert len(findings) == 1
        assert findings[0]["pattern_name"] == "marker"
        assert findings[0]["line_number"] == 1


class TestEstimateAnalysisCost:
    PRICING = {
        "model_name": "MiniMax-M3",
        "input_usd_per_million_tokens": 0.30,
        "output_usd_per_million_tokens": 1.20,
    }

    def test_exact_deterministic_cost(self) -> None:
        # (100_000 * 0.30 + 50_000 * 1.20) / 1_000_000 = 0.09
        estimate = tools.estimate_analysis_cost(100_000, 50_000, self.PRICING)
        assert estimate["estimated_cost_usd"] == 0.09
        assert estimate["currency"] == "USD"
        assert estimate["model_name"] == "MiniMax-M3"
        assert estimate["input_tokens"] == 100_000
        assert estimate["output_token_budget"] == 50_000

    def test_zero_tokens_is_zero_cost(self) -> None:
        estimate = tools.estimate_analysis_cost(0, 0, self.PRICING)
        assert estimate["estimated_cost_usd"] == 0.0

    def test_rounding_documented(self) -> None:
        estimate = tools.estimate_analysis_cost(1, 1, self.PRICING)
        assert round(estimate["estimated_cost_usd"], 6) == estimate["estimated_cost_usd"]

    def test_unknown_model_returns_unpriced_result(self) -> None:
        estimate = tools.estimate_analysis_cost(100, 10, {"model_name": "ghost-model"})
        assert estimate["estimated_cost_usd"] is None
        assert estimate["pricing_configured"] is False

    def test_zero_pricing_returns_unpriced_result(self) -> None:
        pricing = {
            "model_name": "MiniMax-M3",
            "input_usd_per_million_tokens": 0.0,
            "output_usd_per_million_tokens": 1.20,
        }
        estimate = tools.estimate_analysis_cost(100, 10, pricing)
        assert estimate["estimated_cost_usd"] is None
        assert estimate["pricing_configured"] is False

    def test_no_pricing_returns_unpriced_result(self) -> None:
        estimate = tools.estimate_analysis_cost(100, 10, {})
        assert estimate["model_name"] == "unknown"
        assert estimate["estimated_cost_usd"] is None
        assert estimate["pricing_configured"] is False

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValueError):
            tools.estimate_analysis_cost(-1, 10, self.PRICING)
