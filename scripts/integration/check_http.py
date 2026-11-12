"""Exercise the actual web proxy, API and worker using synthetic fixtures only."""
import argparse
import json
import secrets
import time
import tomllib
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]


class ReadingCheck:
    def __init__(self, origin, email, password):
        self.client = httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False, timeout=10)
        self.session_id = None
        login = self.client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, f"Login failed: {login.status_code}"
        self.client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        self.checked = 0

    def request(self, method, path, body=None, expected=200, *, key=None, revision=None):
        headers = {}
        if method != "GET":
            headers["Idempotency-Key"] = key or secrets.token_urlsafe()
            if self.session_id and revision is None:
                revision = self.workspace()["session"]["revision"]
            if revision is not None:
                headers["If-Match"] = f'"{revision}"'
        response = self.client.request(method, "/api/v1" + path, json=body, headers=headers)
        assert response.status_code == expected, f"{method} {path}: expected {expected}, got {response.status_code}, code={response.json().get('error',{}).get('code')}"
        self.checked += 1
        return response

    def workspace(self):
        return self.request("GET", f"/sessions/{self.session_id}").json()

    def job(self, body):
        accepted = self.request("POST", f"/sessions/{self.session_id}/ai-requests", body, 202).json()
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            result = self.request("GET", accepted["poll_url"].removeprefix("/api/v1")).json()
            if result["status"] not in {"queued", "running"}:
                assert result["status"] == "succeeded", f"Job failed: {result['error']}"
                return result
            time.sleep(0.2)
        raise AssertionError("Worker failed to complete synthetic request")

    def review(self):
        data = self.workspace()
        for box in data["boxes"]:
            if box["source"] == "ai" and box["review_action"] == "pending":
                self.request("PATCH", f"/sessions/{self.session_id}/boxes/{box['id']}", {"review_action": "accepted"})
        items = [item | {"action": "accepted" if item["source"] == "ai" else "added"} for item in data["latest_findings"]["items"]]
        self.request("PATCH", f"/sessions/{self.session_id}/findings", {"items": items, "no_abnormality_confirmed": not items})
        version = self.workspace()["latest_findings"]["version"]
        self.request("POST", f"/sessions/{self.session_id}/findings/approve", {"findings_version": version})
        return version

    def check_empty_result(self, study_id, empty_views):
        opened = self.request("POST", f"/studies/{study_id}/sessions", expected=201).json()
        self.session_id = opened["session"]["id"]
        image = next(img for img in opened["images"] if img["view"] in empty_views)
        self.job({"operation": "analyze", "image_id": image["id"]})
        data = self.workspace()
        assert data["latest_findings"]["items"] == [] and data["boxes"] == []
        version = self.review()
        self.job({"operation": "draft_report", "findings_version": version})
        report = self.workspace()["reports"][0]
        self.request("POST", f"/sessions/{self.session_id}/reports/{report['id']}/approve", {"findings_version": version})
        assert self.workspace()["session"]["status"] == "final"

    def run(self):
        patients = self.request("GET", "/patients").json()["items"]
        assert len(patients) == 1
        studies = self.request("GET", f"/patients/{patients[0]['id']}/studies").json()["items"]
        study_id = studies[0]["id"]
        opened = self.request("POST", f"/studies/{study_id}/sessions", expected=201).json()
        self.session_id = opened["session"]["id"]
        assert opened["capabilities"]["provider_mode"] == "mock"
        image = opened["images"][0]
        asset = self.request("GET", image["content_url"].removeprefix("/api/v1"))
        assert asset.headers["content-type"].startswith("image/")
        self.request("POST", f"/sessions/{self.session_id}/ai-requests", {"operation": "draft_report", "findings_version": 1}, 409)
        self.job({"operation": "analyze", "image_id": image["id"]})
        self.request("POST", f"/sessions/{self.session_id}/findings/approve", {"findings_version": 1}, 409)
        self.job({"operation": "ask", "image_id": image["id"], "question": "Describe this synthetic demonstration image."})
        assert [m["role"] for m in self.workspace()["messages"]["items"]] == ["doctor", "ai"]
        box_id = self.request("POST", f"/sessions/{self.session_id}/boxes", {"image_id": image["id"], "bbox_xyxy": [10.2, 20.1, 100.4, 120.6]}, 201).json()["resource_id"]
        self.job({"operation": "ask", "image_id": image["id"], "box_id": box_id, "question": "Describe this synthetic region."})
        data = self.workspace()
        assert data["messages"]["items"][-1]["box_snapshot"]["box_id"] == box_id
        assert data["initial_analysis"]["operation"] == "analyze"
        assert data["initial_analysis"]["provenance"]["vlm_artifact"]
        version = self.review()
        assert all("box_id" in b for b in self.workspace()["latest_findings"]["annotation_snapshot"])
        self.job({"operation": "draft_report", "findings_version": version})
        old_report = self.workspace()["reports"][0]["id"]
        self.request("PATCH", f"/sessions/{self.session_id}/boxes/{box_id}", {"note": "Synthetic evidence correction"})
        assert self.workspace()["reports"][0]["status"] == "stale"
        self.request("POST", f"/sessions/{self.session_id}/reports/{old_report}/approve", {"findings_version": version}, 409)
        version = self.review()
        self.job({"operation": "draft_report", "findings_version": version})
        report = self.workspace()["reports"][0]
        key, revision = secrets.token_urlsafe(), self.workspace()["session"]["revision"]
        path = f"/sessions/{self.session_id}/reports/{report['id']}/approve"
        first = self.request("POST", path, {"findings_version": version}, key=key, revision=revision).json()
        assert self.request("POST", path, {"findings_version": version}, key=key, revision=revision).json() == first
        self.request("POST", f"/sessions/{self.session_id}/ai-requests", {"operation": "ask", "image_id": image["id"], "question": "Synthetic post-final question"}, 409)
        assert self.workspace()["session"]["status"] == "final"
        assert self.workspace()["reports"][0]["content"]["study"]["analyzed_image_id"] == image["id"]
        self.request("GET", "/patients/2/studies", expected=404)
        self.request("GET", f"/sessions/{self.session_id}/events")
        return {"status": "passed", "provider_mode": "mock", "database": "synthetic-preview", "http_assertions": self.checked, "message_count": len(self.workspace()["messages"]["items"]), "final_session": True, "report_version": version}


def synthetic_doctor(admin, origin):
    account = {"name": "HTTP integration checker", "email": f"http-check-{secrets.token_hex(4)}@thorax.demo", "password": secrets.token_urlsafe()}
    identity = admin.request("POST", "/admin/doctors", account, 201).json()["resource_id"]
    admin.request("POST", "/admin/assignments", {"doctor_id": identity, "patient_id": 1}, 201)
    return ReadingCheck(origin, account["email"], account["password"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "infra/local.integration.toml")
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    directory = ROOT / config["runtime"]["directory"]
    credentials = json.loads((directory / "access.json").read_text())
    origin = f"http://{config['network']['host']}:{config['network']['web_port']}"
    admin = ReadingCheck(origin, config["demo"]["admin_email"], credentials["admin_password"])
    check = synthetic_doctor(admin, origin)
    result = check.run()
    empty_views = config["mock"].get("empty_views", [])
    if empty_views:
        study_id = check.workspace()["study"]["id"]
        empty = synthetic_doctor(admin, origin)
        empty.check_empty_result(study_id, empty_views)
        result["empty_analysis_finalized"] = True
        result["empty_analysis_http_assertions"] = empty.checked
    admin.request("GET", "/studies/2411", expected=403)
    result["admin_role_isolation"] = True
    (directory / "http-check.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
