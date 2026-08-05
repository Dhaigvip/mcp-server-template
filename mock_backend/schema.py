"""
Strawberry GraphQL schema mirroring demo-schema.graphql.

Circular type references (Project ↔ Task ↔ Member) are resolved at schema-build
time via typing.get_type_hints — safe because all classes live in this module.

Private fields (strawberry.Private[T]) carry internal state (uid references) that
the resolvers need but that should not appear in the GraphQL schema.

Ordering rule for dataclass fields: required fields (no default) before optional
fields (= None). Private fields are required unless otherwise noted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import strawberry

from mock_backend import fixtures as F

# ── Enums ─────────────────────────────────────────────────────────────────────


@strawberry.enum
class TaskStatus(Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


@strawberry.enum
class Priority(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@strawberry.enum
class MemberRole(Enum):
    VIEWER = "VIEWER"
    CONTRIBUTOR = "CONTRIBUTOR"
    MAINTAINER = "MAINTAINER"
    OWNER = "OWNER"


# ── Filter input ──────────────────────────────────────────────────────────────


@strawberry.input
class ItemFilter:
    codes: Optional[list[str]] = None
    uids: Optional[list[strawberry.ID]] = None
    names: Optional[list[str]] = None
    descriptions: Optional[list[str]] = None
    # None means "no active filter" (show all); True/False filters to that value.
    active: Optional[bool] = None
    priority: Optional[list[Priority]] = None
    due_before: Optional[str] = None
    owner_uid: Optional[list[strawberry.ID]] = None


# ── Output types ──────────────────────────────────────────────────────────────


@strawberry.type
class ChecklistItem:
    uid: strawberry.ID
    text: str
    done: bool


@strawberry.type
class Label:
    uid: strawberry.ID
    name: str
    colour: str
    active: bool
    usage_count: int


@strawberry.type
class AuditEntry:
    uid: strawberry.ID
    entity_type: str
    entity_uid: strawberry.ID
    action: str
    actor_uid: strawberry.ID
    occurred_at: str


@strawberry.type
class Member:
    uid: strawberry.ID
    name: str
    email: str
    role: MemberRole
    active: bool
    job_title: Optional[str] = None
    capacity_hours: Optional[float] = None

    @strawberry.field
    def assigned_tasks(self) -> list[Task]:
        return [
            task_from_dict(t) for t in F.TASKS.values() if t.get("assigneeUid") == str(self.uid)
        ]


@strawberry.type
class Milestone:
    uid: strawberry.ID
    name: str
    target_date: str
    reached: bool
    # private — uid references for lazy resolution
    project_uid: strawberry.Private[str]
    task_uids: strawberry.Private[list[str]]

    @strawberry.field
    def project(self) -> Project:
        return project_from_dict(F.PROJECTS[self.project_uid])

    @strawberry.field
    def tasks(self) -> list[Task]:
        return [task_from_dict(F.TASKS[uid]) for uid in self.task_uids if uid in F.TASKS]


@strawberry.type
class Task:
    uid: strawberry.ID
    code: str
    title: str
    active: bool
    status: TaskStatus
    priority: Priority
    logged_hours: float
    sort_order: int
    # private — placed before optional scalars to satisfy dataclass ordering
    project_uid: strawberry.Private[str]
    assignee_uid: strawberry.Private[Optional[str]]
    label_uids: strawberry.Private[list[str]]
    checklist_items: strawberry.Private[list[dict]]
    blocked_by_uids: strawberry.Private[list[str]]
    description: Optional[str] = None
    estimate_hours: Optional[float] = None
    due_date: Optional[str] = None

    @strawberry.field
    def project(self) -> Project:
        return project_from_dict(F.PROJECTS[self.project_uid])

    @strawberry.field
    def assignee(self) -> Optional[Member]:
        if self.assignee_uid:
            return member_from_dict(F.MEMBERS[self.assignee_uid])
        return None

    @strawberry.field
    def labels(self) -> list[Label]:
        return [label_from_dict(F.LABELS[uid]) for uid in self.label_uids if uid in F.LABELS]

    @strawberry.field
    def checklist(self) -> list[ChecklistItem]:
        return [
            ChecklistItem(uid=c["uid"], text=c["text"], done=c["done"])
            for c in self.checklist_items
        ]

    @strawberry.field
    def blocked_by(self) -> list[Task]:
        return [task_from_dict(F.TASKS[uid]) for uid in self.blocked_by_uids if uid in F.TASKS]


@strawberry.type
class Project:
    uid: strawberry.ID
    code: str
    name: str
    active: bool
    priority: Priority
    progress: float
    created_at: str
    # private — placed before optional scalars to satisfy dataclass ordering
    owner_uid: strawberry.Private[Optional[str]]
    label_uids: strawberry.Private[list[str]]
    milestone_uids: strawberry.Private[list[str]]
    description: Optional[str] = None
    due_date: Optional[str] = None
    budget: Optional[float] = None

    @strawberry.field
    def owner(self) -> Optional[Member]:
        if self.owner_uid:
            return member_from_dict(F.MEMBERS[self.owner_uid])
        return None

    @strawberry.field
    def tasks(self) -> list[Task]:
        return [task_from_dict(t) for t in F.TASKS.values() if t["projectUid"] == str(self.uid)]

    @strawberry.field
    def milestones(self) -> list[Milestone]:
        return [
            milestone_from_dict(F.MILESTONES[uid])
            for uid in self.milestone_uids
            if uid in F.MILESTONES
        ]

    @strawberry.field
    def labels(self) -> list[Label]:
        return [label_from_dict(F.LABELS[uid]) for uid in self.label_uids if uid in F.LABELS]


@strawberry.type
class Organization:
    uid: strawberry.ID
    name: str
    slug: str
    timezone: str
    created_at: str
    project_count: int

    @strawberry.field
    def projects(self) -> list[Project]:
        return [project_from_dict(p) for p in F.PROJECTS.values()]

    @strawberry.field
    def members(self) -> list[Member]:
        return [member_from_dict(m) for m in F.MEMBERS.values()]


# ── Payload types ─────────────────────────────────────────────────────────────


@strawberry.type
class ProjectPayload:
    ok: bool
    message: Optional[str] = None
    project: Optional[Project] = None


@strawberry.type
class TaskPayload:
    ok: bool
    message: Optional[str] = None
    task: Optional[Task] = None


@strawberry.type
class MemberPayload:
    ok: bool
    message: Optional[str] = None
    member: Optional[Member] = None


@strawberry.type
class LabelPayload:
    ok: bool
    message: Optional[str] = None
    label: Optional[Label] = None


@strawberry.type
class MilestonePayload:
    ok: bool
    message: Optional[str] = None
    milestone: Optional[Milestone] = None


# ── Mutation input types ──────────────────────────────────────────────────────


@strawberry.input
class ProjectCreateInput:
    name: str
    description: Optional[str] = None
    priority: Optional[Priority] = None
    due_date: Optional[str] = None
    budget: Optional[float] = None
    owner_uid: Optional[strawberry.ID] = None
    label_uids: Optional[list[strawberry.ID]] = None


@strawberry.input
class ProjectUpdateInput:
    uid: strawberry.ID
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    priority: Optional[Priority] = None
    due_date: Optional[str] = None
    budget: Optional[float] = None
    owner_uid: Optional[strawberry.ID] = None
    label_uids: Optional[list[strawberry.ID]] = None


@strawberry.input
class ChecklistItemInput:
    text: str
    done: Optional[bool] = None


@strawberry.input
class TaskCreateInput:
    project_uid: strawberry.ID
    title: str
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[Priority] = None
    estimate_hours: Optional[float] = None
    due_date: Optional[str] = None
    assignee_uid: Optional[strawberry.ID] = None
    label_uids: Optional[list[strawberry.ID]] = None
    checklist: Optional[list[ChecklistItemInput]] = None


@strawberry.input
class TaskUpdateInput:
    uid: strawberry.ID
    title: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    status: Optional[TaskStatus] = None
    priority: Optional[Priority] = None
    estimate_hours: Optional[float] = None
    logged_hours: Optional[float] = None
    due_date: Optional[str] = None
    sort_order: Optional[int] = None
    assignee_uid: Optional[strawberry.ID] = None
    label_uids: Optional[list[strawberry.ID]] = None
    checklist: Optional[list[ChecklistItemInput]] = None


@strawberry.input
class MemberUpdateInput:
    uid: strawberry.ID
    name: Optional[str] = None
    role: Optional[MemberRole] = None
    active: Optional[bool] = None
    job_title: Optional[str] = None
    capacity_hours: Optional[float] = None


@strawberry.input
class LabelCreateInput:
    name: str
    colour: str


@strawberry.input
class LabelUpdateInput:
    uid: strawberry.ID
    name: Optional[str] = None
    colour: Optional[str] = None
    active: Optional[bool] = None


@strawberry.input
class MilestoneCreateInput:
    project_uid: strawberry.ID
    name: str
    target_date: str


@strawberry.input
class MilestoneUpdateInput:
    uid: strawberry.ID
    name: Optional[str] = None
    target_date: Optional[str] = None
    reached: Optional[bool] = None


# ── Factory helpers ───────────────────────────────────────────────────────────


def member_from_dict(d: dict) -> Member:
    return Member(
        uid=d["uid"],
        name=d["name"],
        email=d["email"],
        role=MemberRole(d["role"]),
        active=d["active"],
        job_title=d.get("jobTitle"),
        capacity_hours=d.get("capacityHours"),
    )


def label_from_dict(d: dict) -> Label:
    return Label(
        uid=d["uid"],
        name=d["name"],
        colour=d["colour"],
        active=d["active"],
        usage_count=d["usageCount"],
    )


def milestone_from_dict(d: dict) -> Milestone:
    return Milestone(
        uid=d["uid"],
        name=d["name"],
        target_date=d["targetDate"],
        reached=d["reached"],
        project_uid=d["projectUid"],
        task_uids=d["taskUids"],
    )


def task_from_dict(d: dict) -> Task:
    return Task(
        uid=d["uid"],
        code=d["code"],
        title=d["title"],
        active=d.get("active", True),
        status=TaskStatus(d["status"]),
        priority=Priority(d["priority"]),
        logged_hours=d.get("loggedHours", 0.0),
        sort_order=d.get("sortOrder", 0),
        project_uid=d["projectUid"],
        assignee_uid=d.get("assigneeUid"),
        label_uids=d.get("labelUids", []),
        checklist_items=d.get("checklistItems", []),
        blocked_by_uids=d.get("blockedByUids", []),
        description=d.get("description"),
        estimate_hours=d.get("estimateHours"),
        due_date=d.get("dueDate"),
    )


def project_from_dict(d: dict) -> Project:
    return Project(
        uid=d["uid"],
        code=d["code"],
        name=d["name"],
        active=d.get("active", True),
        priority=Priority(d["priority"]),
        progress=d.get("progress", 0.0),
        created_at=d["createdAt"],
        owner_uid=d.get("ownerUid"),
        label_uids=d.get("labelUids", []),
        milestone_uids=d.get("milestoneUids", []),
        description=d.get("description"),
        due_date=d.get("dueDate"),
        budget=d.get("budget"),
    )


# ── Filter helper ─────────────────────────────────────────────────────────────


def _apply_filter(
    items: list[dict], f: Optional[ItemFilter], active_default: bool = True
) -> list[dict]:
    """
    Apply ItemFilter to a list of raw fixture dicts.

    active_default controls behaviour when no filter is provided (f is None):
    True → show only active items (matches the schema's "active-only by default" contract).
    When f is provided but f.active is None → no active filtering (show all).
    """
    result = list(items)

    if f is None:
        if active_default:
            result = [i for i in result if i.get("active", True)]
        return result

    if f.active is not None:
        result = [i for i in result if i.get("active", True) == f.active]
    if f.uids:
        uid_set = {str(u) for u in f.uids}
        result = [i for i in result if i["uid"] in uid_set]
    if f.codes:
        code_set = set(f.codes)
        result = [i for i in result if i.get("code", "") in code_set]
    if f.names:
        # Every fixture entity has a "display name" field, but it isn't
        # always literally called "name" — Task uses "title" (matches
        # demo-schema.graphql's own field naming). Fall back so the
        # universal `names` filter actually works for Task/Project-shaped
        # queries too, not just Member/Label/Milestone.
        result = [
            i
            for i in result
            if any(
                str(i.get("name") or i.get("title") or "").lower().startswith(n.lower())
                for n in f.names
            )
        ]
    if f.descriptions:
        result = [
            i
            for i in result
            if any(q.lower() in (i.get("description") or "").lower() for q in f.descriptions)
        ]
    if f.priority:
        prio_vals = {p.value for p in f.priority}
        result = [i for i in result if i.get("priority") in prio_vals]
    if f.due_before:
        result = [i for i in result if (i.get("dueDate") or "9999") <= f.due_before]
    if f.owner_uid:
        uid_set = {str(u) for u in f.owner_uid}
        result = [
            i for i in result if i.get("ownerUid") in uid_set or i.get("assigneeUid") in uid_set
        ]
    return result


# ── Query ─────────────────────────────────────────────────────────────────────


@strawberry.type
class Query:
    @strawberry.field
    def current_organization(self) -> Optional[Organization]:
        return Organization(
            uid=F.ORGANIZATION["uid"],
            name=F.ORGANIZATION["name"],
            slug=F.ORGANIZATION["slug"],
            timezone=F.ORGANIZATION["timezone"],
            created_at=F.ORGANIZATION["createdAt"],
            project_count=F.ORGANIZATION["projectCount"],
        )

    @strawberry.field
    def projects(self, filter: Optional[ItemFilter] = None) -> list[Project]:
        return [project_from_dict(p) for p in _apply_filter(list(F.PROJECTS.values()), filter)]

    @strawberry.field
    def tasks(self, filter: Optional[ItemFilter] = None) -> list[Task]:
        return [task_from_dict(t) for t in _apply_filter(list(F.TASKS.values()), filter)]

    @strawberry.field
    def members(self, filter: Optional[ItemFilter] = None) -> list[Member]:
        return [member_from_dict(m) for m in _apply_filter(list(F.MEMBERS.values()), filter)]

    @strawberry.field
    def labels(self, filter: Optional[ItemFilter] = None) -> list[Label]:
        return [label_from_dict(lb) for lb in _apply_filter(list(F.LABELS.values()), filter)]

    @strawberry.field
    def milestones(self, filter: Optional[ItemFilter] = None) -> list[Milestone]:
        return [milestone_from_dict(m) for m in _apply_filter(list(F.MILESTONES.values()), filter)]

    @strawberry.field
    def audit_entries(self, filter: Optional[ItemFilter] = None) -> list[AuditEntry]:
        # AuditEntry has no active field — skip active_default filtering
        filtered = _apply_filter(list(F.AUDIT_ENTRIES.values()), filter, active_default=False)
        return [
            AuditEntry(
                uid=e["uid"],
                entity_type=e["entityType"],
                entity_uid=e["entityUid"],
                action=e["action"],
                actor_uid=e["actorUid"],
                occurred_at=e["occurredAt"],
            )
            for e in filtered
        ]


# ── Mutation ──────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_uid() -> str:
    return uuid.uuid4().hex[:8]


@strawberry.type
class Mutation:
    @strawberry.mutation
    def create_project(self, input: ProjectCreateInput) -> ProjectPayload:
        new_uid = f"p-{_short_uid()}"
        d: dict = {
            "uid": new_uid,
            "code": f"PRJ-{len(F.PROJECTS) + 1:03d}",
            "name": input.name,
            "description": input.description,
            "active": True,
            "priority": input.priority.value if input.priority else "MEDIUM",
            "dueDate": input.due_date,
            "progress": 0.0,
            "budget": input.budget,
            "createdAt": _now(),
            "ownerUid": str(input.owner_uid) if input.owner_uid else None,
            "labelUids": [str(u) for u in (input.label_uids or [])],
            "milestoneUids": [],
        }
        F.PROJECTS[new_uid] = d
        return ProjectPayload(
            ok=True, message=f"Project '{input.name}' created.", project=project_from_dict(d)
        )

    @strawberry.mutation
    def update_project(self, input: ProjectUpdateInput) -> ProjectPayload:
        uid = str(input.uid)
        if uid not in F.PROJECTS:
            return ProjectPayload(ok=False, message=f"Project '{uid}' not found.")
        d = F.PROJECTS[uid]
        if input.name is not None:
            d["name"] = input.name
        if input.description is not None:
            d["description"] = input.description
        if input.active is not None:
            d["active"] = input.active
        if input.priority is not None:
            d["priority"] = input.priority.value
        if input.due_date is not None:
            d["dueDate"] = input.due_date
        if input.budget is not None:
            d["budget"] = input.budget
        if input.owner_uid is not None:
            d["ownerUid"] = str(input.owner_uid)
        if input.label_uids is not None:
            d["labelUids"] = [str(u) for u in input.label_uids]
        return ProjectPayload(ok=True, message="Project updated.", project=project_from_dict(d))

    @strawberry.mutation
    def create_task(self, input: TaskCreateInput) -> TaskPayload:
        project_uid = str(input.project_uid)
        if project_uid not in F.PROJECTS:
            return TaskPayload(ok=False, message=f"Project '{project_uid}' not found.")
        new_uid = f"t-{_short_uid()}"
        checklist = [
            {"uid": f"ci-{_short_uid()}", "text": item.text, "done": item.done or False}
            for item in (input.checklist or [])
        ]
        d: dict = {
            "uid": new_uid,
            "code": f"TSK-{len(F.TASKS) + 1:03d}",
            "title": input.title,
            "description": input.description,
            "active": True,
            "status": input.status.value if input.status else "TODO",
            "priority": input.priority.value if input.priority else "MEDIUM",
            "estimateHours": input.estimate_hours,
            "loggedHours": 0.0,
            "dueDate": input.due_date,
            "sortOrder": sum(1 for t in F.TASKS.values() if t["projectUid"] == project_uid),
            "projectUid": project_uid,
            "assigneeUid": str(input.assignee_uid) if input.assignee_uid else None,
            "labelUids": [str(u) for u in (input.label_uids or [])],
            "checklistItems": checklist,
            "blockedByUids": [],
        }
        F.TASKS[new_uid] = d
        return TaskPayload(
            ok=True, message=f"Task '{input.title}' created.", task=task_from_dict(d)
        )

    @strawberry.mutation
    def update_task(self, input: TaskUpdateInput) -> TaskPayload:
        uid = str(input.uid)
        if uid not in F.TASKS:
            return TaskPayload(ok=False, message=f"Task '{uid}' not found.")
        d = F.TASKS[uid]
        if input.title is not None:
            d["title"] = input.title
        if input.description is not None:
            d["description"] = input.description
        if input.active is not None:
            d["active"] = input.active
        if input.status is not None:
            d["status"] = input.status.value
        if input.priority is not None:
            d["priority"] = input.priority.value
        if input.estimate_hours is not None:
            d["estimateHours"] = input.estimate_hours
        if input.logged_hours is not None:
            d["loggedHours"] = input.logged_hours
        if input.due_date is not None:
            d["dueDate"] = input.due_date
        if input.sort_order is not None:
            d["sortOrder"] = input.sort_order
        if input.assignee_uid is not None:
            d["assigneeUid"] = str(input.assignee_uid)
        if input.label_uids is not None:
            d["labelUids"] = [str(u) for u in input.label_uids]
        if input.checklist is not None:
            d["checklistItems"] = [
                {"uid": f"ci-{_short_uid()}", "text": item.text, "done": item.done or False}
                for item in input.checklist
            ]
        return TaskPayload(ok=True, message="Task updated.", task=task_from_dict(d))

    @strawberry.mutation
    def update_member(self, input: MemberUpdateInput) -> MemberPayload:
        uid = str(input.uid)
        if uid not in F.MEMBERS:
            return MemberPayload(ok=False, message=f"Member '{uid}' not found.")
        d = F.MEMBERS[uid]
        if input.name is not None:
            d["name"] = input.name
        if input.role is not None:
            d["role"] = input.role.value
        if input.active is not None:
            d["active"] = input.active
        if input.job_title is not None:
            d["jobTitle"] = input.job_title
        if input.capacity_hours is not None:
            d["capacityHours"] = input.capacity_hours
        return MemberPayload(ok=True, message="Member updated.", member=member_from_dict(d))

    @strawberry.mutation
    def create_label(self, input: LabelCreateInput) -> LabelPayload:
        new_uid = f"l-{_short_uid()}"
        d: dict = {
            "uid": new_uid,
            "name": input.name,
            "colour": input.colour,
            "active": True,
            "usageCount": 0,
        }
        F.LABELS[new_uid] = d
        return LabelPayload(
            ok=True, message=f"Label '{input.name}' created.", label=label_from_dict(d)
        )

    @strawberry.mutation
    def update_label(self, input: LabelUpdateInput) -> LabelPayload:
        uid = str(input.uid)
        if uid not in F.LABELS:
            return LabelPayload(ok=False, message=f"Label '{uid}' not found.")
        d = F.LABELS[uid]
        if input.name is not None:
            d["name"] = input.name
        if input.colour is not None:
            d["colour"] = input.colour
        if input.active is not None:
            d["active"] = input.active
        return LabelPayload(ok=True, message="Label updated.", label=label_from_dict(d))

    @strawberry.mutation
    def create_milestone(self, input: MilestoneCreateInput) -> MilestonePayload:
        project_uid = str(input.project_uid)
        if project_uid not in F.PROJECTS:
            return MilestonePayload(ok=False, message=f"Project '{project_uid}' not found.")
        new_uid = f"ms-{_short_uid()}"
        d: dict = {
            "uid": new_uid,
            "name": input.name,
            "targetDate": input.target_date,
            "reached": False,
            "projectUid": project_uid,
            "taskUids": [],
        }
        F.MILESTONES[new_uid] = d
        F.PROJECTS[project_uid]["milestoneUids"].append(new_uid)
        return MilestonePayload(
            ok=True, message=f"Milestone '{input.name}' created.", milestone=milestone_from_dict(d)
        )

    @strawberry.mutation
    def update_milestone(self, input: MilestoneUpdateInput) -> MilestonePayload:
        uid = str(input.uid)
        if uid not in F.MILESTONES:
            return MilestonePayload(ok=False, message=f"Milestone '{uid}' not found.")
        d = F.MILESTONES[uid]
        if input.name is not None:
            d["name"] = input.name
        if input.target_date is not None:
            d["targetDate"] = input.target_date
        if input.reached is not None:
            d["reached"] = input.reached
        return MilestonePayload(
            ok=True, message="Milestone updated.", milestone=milestone_from_dict(d)
        )


schema = strawberry.Schema(query=Query, mutation=Mutation)
