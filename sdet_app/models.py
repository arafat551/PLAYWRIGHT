"""Lightweight row models. Each entity can be built from a sqlite3.Row."""


class User:
    def __init__(self, id, email, created_at="", full_name="", role="qa", is_active=1):
        self.id = id
        self.email = email
        self.created_at = created_at
        self.full_name = full_name
        self.role = role
        self.is_active = is_active

    @classmethod
    def from_row(cls, row):
        if not row:
            return None
        d = dict(row)
        return cls(d["id"], d["email"], d.get("created_at", ""),
                   d.get("full_name", ""), d.get("role", "qa"),
                   d.get("is_active", 1))


class Project:
    def __init__(self, row):
        self.row = dict(row)

    @classmethod
    def from_row(cls, row):
        return cls(row)

    def __getitem__(self, key):
        return self.row[key]

    @property
    def id(self):
        return self.row["id"]

    @property
    def name(self):
        return self.row.get("name", "")


class ProjectPage:
    def __init__(self, id, name, url, last_status=0):
        self.id = id
        self.name = name
        self.url = url
        self.last_status = last_status

    @classmethod
    def from_row(cls, row):
        return cls(row["id"], row["name"], row["url"], row["last_status"])


class PageAction:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @classmethod
    def from_row(cls, row):
        return cls(**dict(row))


class TestRun:
    def __init__(self, id, **kw):
        self.id = id
        for k, v in kw.items():
            setattr(self, k, v)
        if not hasattr(self, "project_env"):
            self.project_env = ""

    @classmethod
    def from_row(cls, row):
        return cls(**dict(row))


class TestResult:
    def __init__(self, id, **kw):
        self.id = id
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def function(self):
        if getattr(self, "function_name", None):
            return self.function_name
        return getattr(self, "_function", "")

    @classmethod
    def from_row(cls, row):
        return cls(**dict(row))
