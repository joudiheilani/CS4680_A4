"""Sample files and tasks every new workspace starts with, so there is something to demo.

Each file is small (the model only sees the first few thousand characters of a file) and
is chosen to show off a different kind of request:

- notes/data_structures.md  summarise it, make a cheat sheet, turn topics into tasks
- grades.csv                read it, calculate a weighted grade, save a report
- schedule.csv              "when is my next class?", plan study time around classes
- flashcards.json           quiz me, add a flashcard (edit a JSON file)
- reading_list.txt          append to it, turn it into tasks
- essay_draft.md            proofread it and overwrite it (asks for confirmation first)
- old_scratch.txt           obviously disposable: the safe thing to delete in a demo
"""

from __future__ import annotations

from datetime import date, timedelta

DATA_STRUCTURES = """# CS2400 Data Structures: unit 4 notes

## Graphs
- A graph is a set of vertices (nodes) joined by edges. Edges can be directed or undirected, weighted or unweighted.
- Adjacency list: each vertex keeps a list of its neighbours. Uses O(V + E) space, best for sparse graphs.
- Adjacency matrix: a V x V grid. O(1) edge lookup but O(V^2) space.

## Traversal
- BFS (breadth-first search) uses a queue and visits nodes level by level. Finds shortest paths in unweighted graphs. O(V + E).
- DFS (depth-first search) uses a stack or recursion and goes as deep as possible first. Used for cycle detection and topological sort. O(V + E).

## Shortest paths
- Dijkstra's algorithm: weighted graphs with no negative edges. Uses a priority queue. O((V + E) log V).
- Bellman-Ford handles negative edges and detects negative cycles. O(V * E).

## Hash tables
- Map keys to buckets with a hash function. Average O(1) insert, lookup and delete.
- Collisions are handled by chaining (lists in each bucket) or open addressing (probe for the next free slot).
- Keep the load factor below about 0.75 and resize to stay fast.

Quiz on graphs and hashing next week. Professor said Dijkstra will definitely be on it.
"""

GRADES = """course,assignment,score,max_score,weight_percent
CS4680,Assignment 1,92,100,10
CS4680,Assignment 2,85,100,10
CS4680,Assignment 3,88,100,10
CS4680,Midterm exam,79,100,30
CS2400,Lab 1,18,20,5
CS2400,Lab 2,20,20,5
CS2400,Quiz 1,7,10,10
CS2400,Midterm exam,84,100,25
"""

SCHEDULE = """course,title,days,start,end,room
CS4680,Prompt Engineering,Tue/Thu,10:00,11:15,Bldg 8 Room 302
CS2400,Data Structures,Mon/Wed,13:00,14:15,Bldg 98 Room 4
MAT2250,Linear Algebra,Mon/Wed/Fri,09:00,09:50,Bldg 3 Room 1015
PHY1510,Physics Lab,Fri,14:00,16:50,Bldg 8 Room 115
"""

FLASHCARDS = """[
  {"term": "Zero-shot prompting", "definition": "Asking the model to do a task with instructions only, no examples."},
  {"term": "Few-shot prompting", "definition": "Including a few worked examples in the prompt so the model copies the pattern."},
  {"term": "Chain of thought", "definition": "Prompting the model to reason step by step before answering."},
  {"term": "ReAct", "definition": "Reason + Act: the model alternates thoughts, tool calls and observations."},
  {"term": "Prompt injection", "definition": "Untrusted text that tries to override the system's instructions."}
]
"""

READING_LIST = """Reading list (fall semester)
- Data Structures textbook, chapter 5: Graphs
- Data Structures textbook, chapter 6: Hash tables
- "Attention Is All You Need" (Vaswani et al., 2017)
- "ReAct: Synergizing Reasoning and Acting in Language Models" (Yao et al., 2022)
"""

ESSAY_DRAFT = """# Should students use AI tools for homework? (draft)

AI tools like chatbots is becoming a normal part of studying. Many students use them to explain hard
topics, check there work, or get unstuck at 2am when office hours are closed.

The problem is when students let the tool do the thinking for them. If you copy an answer you dont
learn anything, and the exam will show it. AI also makes mistakes confidently, so you has to check it.

I think the best approach is to treat AI like a tutor, not a ghostwriter: ask it to explain, quiz you,
and give feedback, but write the final work yourself.
"""

OLD_SCRATCH = """scratch - delete me later
- wifi password for the library is on the sign by the door
- remember to return the calculator to Sam
- random idea: app that tracks study streaks?
"""

SAMPLE_FILES: dict[str, str] = {
    "notes/data_structures.md": DATA_STRUCTURES,
    "grades.csv": GRADES,
    "schedule.csv": SCHEDULE,
    "flashcards.json": FLASHCARDS,
    "reading_list.txt": READING_LIST,
    "essay_draft.md": ESSAY_DRAFT,
    "old_scratch.txt": OLD_SCRATCH,
}


def sample_tasks(today: date | None = None) -> list[dict]:
    """Example to-do items, dated relative to today so they always look current."""
    today = today or date.today()

    def d(days: int) -> str:
        return (today + timedelta(days=days)).isoformat()

    return [
        {"title": "Finish the CS4680 A4 write-up", "due": d(2), "priority": "high"},
        {"title": "Record the 2-minute demo video", "due": d(7), "priority": "high"},
        {"title": "Read chapter 5 (Graphs)", "due": d(3), "priority": "medium"},
        {"title": "Email lab partner about the physics report", "due": d(1), "priority": "low"},
        {"title": "Buy a new lab notebook", "due": None, "priority": "low"},
        {"title": "Submit CS2400 problem set 3", "due": d(-1), "priority": "medium", "done": True},
    ]
