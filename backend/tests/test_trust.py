from release_assistant.application.trust import TrustTracker


def test_fresh_tracker_reports_trusted_context() -> None:
    assert TrustTracker().context().untrusted_content_seen is False


def test_marking_makes_the_context_untrusted_and_records_the_source_once() -> None:
    tracker = TrustTracker()

    tracker.mark_untrusted("customer_reviews")
    tracker.mark_untrusted("customer_reviews")
    tracker.mark_untrusted("pasted_message")

    assert tracker.context().untrusted_content_seen is True
    assert tracker.untrusted_sources == ("customer_reviews", "pasted_message")
