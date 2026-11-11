"""Sample files and tasks every new workspace starts with, so there is something to demo.

Each file is small (the model only sees the first few thousand characters of a file) and
is chosen to show off a different kind of request:

- notes/cs3310_final.md     summarise it, make a cheat sheet, turn topics into study tasks
- grades.csv                read it, calculate a weighted grade, save a report
- schedule.csv              "when is my next class?", plan study time around classes
- flashcards.json           quiz me, add a flashcard (edit a JSON file)
- reading_list.txt          append to it, turn it into tasks
- old_scratch.txt           obviously disposable: the safe thing to delete in a demo
"""

from __future__ import annotations

from datetime import date, timedelta

CS3310_FINAL = """# CS3310 Design and Analysis of Algorithms: final exam study guide

Final exam: {final_day}. Cumulative, but most of it is on the second half of the course.

## Asymptotic analysis
- Big-O (upper bound), Big-Omega (lower bound), Big-Theta (tight bound).
- Master theorem for T(n) = aT(n/b) + f(n): compare f(n) with n^(log_b a).

## Divide and conquer
- Merge sort O(n log n), quicksort O(n log n) average / O(n^2) worst.
- Lower bound for comparison sorting is Omega(n log n).

## Greedy algorithms
- Make the locally best choice; prove it with an exchange argument.
- Examples: activity selection, Huffman coding, Kruskal and Prim for minimum spanning trees.

## Dynamic programming
- Optimal substructure + overlapping subproblems. Memoize (top-down) or tabulate (bottom-up).
- Examples: longest common subsequence, 0/1 knapsack, edit distance, matrix-chain multiplication.

## Graph algorithms
- BFS / DFS O(V + E); topological sort with DFS on a DAG.
- Dijkstra (no negative edges) O((V + E) log V); Bellman-Ford O(VE) handles negative edges.
- Floyd-Warshall all-pairs shortest paths O(V^3).

## NP-completeness
- P, NP, NP-hard, NP-complete. Reductions show a problem is at least as hard as another.
- Classic NP-complete problems: SAT, 3-SAT, vertex cover, Hamiltonian cycle, subset sum.

Professor said DP and graph algorithms are about half the exam.
"""

GRADES = """course,assignment,score,max_score,weight_percent
CS4680,Assignment 1,92,100,15
CS4680,Assignment 2,85,100,15
CS4680,Assignment 3,88,100,15
CS4680,Research report proposal,90,100,10
CS3310,Homework 1,45,50,10
CS3310,Homework 2,38,50,10
CS3310,Homework 3,47,50,10
CS3310,Midterm exam,81,100,25
CS4200,Homework 1,19,20,10
CS4200,Project proposal,95,100,10
"""

SCHEDULE = """course,title,days,start,end,room
CS4680,Prompt Engineering,Tue/Thu,10:00,11:15,Bldg 8 Room 302
CS3310,Design and Analysis of Algorithms,Mon/Wed,13:00,14:15,Bldg 98 Room 4
CS4200,Artificial Intelligence,Tue/Thu,14:30,15:45,Bldg 8 Room 345
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
- Introduction to Algorithms (CLRS), chapter 15: Dynamic programming
- Introduction to Algorithms (CLRS), chapter 24: Single-source shortest paths
- Artificial Intelligence: A Modern Approach, chapter 3: Solving problems by searching
- "Attention Is All You Need" (Vaswani et al., 2017)
- "ReAct: Synergizing Reasoning and Acting in Language Models" (Yao et al., 2022)
"""

OLD_SCRATCH = """scratch - delete me later
- wifi password for the library is on the sign by the door
- remember to return the calculator to Sam
- random idea: app that tracks study streaks?
"""

SAMPLE_FILES: dict[str, str] = {
    "notes/cs3310_final.md": CS3310_FINAL,
    "grades.csv": GRADES,
    "schedule.csv": SCHEDULE,
    "flashcards.json": FLASHCARDS,
    "reading_list.txt": READING_LIST,
    "old_scratch.txt": OLD_SCRATCH,
}


def final_exam_day(today: date | None = None) -> date:
    """The CS3310 final in the sample notes: always two weeks out, so the demo stays current."""
    return (today or date.today()) + timedelta(days=14)


def sample_files(today: date | None = None) -> dict[str, str]:
    final = final_exam_day(today)
    day = f"{final.strftime('%A, %B')} {final.day}"
    return {rel: text.replace("{final_day}", day) for rel, text in SAMPLE_FILES.items()}


def sample_tasks(today: date | None = None) -> list[dict]:
    """Example to-do items, dated relative to today so they always look current."""
    today = today or date.today()

    def d(days: int) -> str:
        return (today + timedelta(days=days)).isoformat()

    return [
        {"title": "Work on research report", "due": d(2), "priority": "high"},
        {"title": "Record the 2-minute demo video", "due": d(0), "priority": "high"},
        {"title": "Finish the CS4200 AI project", "due": d(7), "priority": "high"},
        {"title": "Review graph algorithms for the CS3310 final", "due": d(3), "priority": "medium"},
        {"title": "Email lab partner about the physics report", "due": d(1), "priority": "low"},
        {"title": "Buy a new lab notebook", "due": None, "priority": "low"},
        {"title": "Submit CS3310 homework 3", "due": d(-1), "priority": "medium", "done": True},
    ]
