def test_actions_from_allowed_states_only(env):  # round 3, P1
    failed = env.card_in_status("failed")
    assert env.post(failed, "discard").status_code == 303
    recon = env.card_in_status("needs-reconciliation")
    assert env.post(recon, "check-again").status_code == 303
    p = env.any_pending()
    assert env.post(p, "retry").status_code == 409
    assert env.post(p, "check-again").status_code == 409
    assert env.post(p, "acknowledge").status_code == 409


def test_failed_card_shows_message_retry_and_discard(env):
    failed = env.card_in_status("failed")
    html = env.client.get(f"/?card={failed.id}").text
    assert "Failed at step 1 of 1: boom" in html and ">Retry<" in html and ">Discard<" in html
