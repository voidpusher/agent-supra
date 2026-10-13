import uuid


class EventLog:
    """Builds event lists for detector tests."""

    def __init__(self):
        self.events = []

    def _add(self, type_, **data):
        e = {"event_id": uuid.uuid4().hex[:8], "seq": len(self.events) + 1, "type": type_, "data": data}
        self.events.append(e)
        return e

    def tests(self, failing, rev, suite=("t::a", "t::b", "t::c", "t::d"), transient=False):
        data = {"suite": list(suite), "failing": list(failing), "rev": rev}
        if transient:
            data["error_class"] = "transient"
        return self._add("tests.reported", **data)

    def call(self, input_tokens, output_tokens=500):
        return self._add("model.call", model="demo-model", input_tokens=input_tokens,
                         output_tokens=output_tokens, cost_micros=None)

    def tool(self, ok, rev="r1", args_fp="a1", error_fp="e1", error_class="permanent", tool="run"):
        return self._add("tool.finished", tool=tool, ok=ok, rev=rev, args_fp=args_fp,
                         error_fp=error_fp, error_class=error_class)
