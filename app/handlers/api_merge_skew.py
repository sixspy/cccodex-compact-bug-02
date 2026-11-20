from app.handlers.base import BaseHandler
from app.services.merge_skew_ledger import append_ledger, dashboard
from app.services.merge_skew_surface import reconcile_surface, snapshot


class ApiMergeSkewDash(BaseHandler):
    def get(self):
        self.require_user()
        self.json(dashboard())


class ApiMergeSkewNote(BaseHandler):
    def post(self):
        self.require_user()
        body = self.read_json()
        self.json(
            append_ledger(
                int(body.get("plan_id") or 0),
                str(body.get("note") or "合并偏斜"),
            )
        )


class ApiMergeSkewHalf(BaseHandler):
    def post(self):
        self.require_user()
        self.json(reconcile_surface())


class ApiMergeSkewSnap(BaseHandler):
    def get(self):
        self.require_user()
        self.json(snapshot())
