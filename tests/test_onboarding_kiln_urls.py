"""The Kiln web UI links the start-up checks print."""

from kiln_harness_tuner.onboarding import kiln_urls

HOST = "http://localhost:8757"


def test_each_link_names_its_kiln_page():
    assert kiln_urls.create_project(HOST) == f"{HOST}/settings/create_project"
    assert kiln_urls.create_task(HOST, "p1") == f"{HOST}/settings/create_task/p1"
    assert kiln_urls.run_configs(HOST, "p1", "t1") == f"{HOST}/optimize/p1/t1"
    assert kiln_urls.evals(HOST, "p1", "t1") == f"{HOST}/specs/p1/t1"
    assert kiln_urls.providers(HOST) == f"{HOST}/settings/providers"
