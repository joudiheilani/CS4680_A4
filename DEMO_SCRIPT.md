# 2-minute demo script

Record the deployed app with a screen recorder (Windows: Win+Alt+R with Xbox Game Bar, or OBS). Before recording, press **Reset** so the workspace is fresh, and send one throwaway message so the free Render instance is awake.

| Time | Do | Say |
|---|---|---|
| 0:00–0:15 | Show the empty app | "This is StudyPilot, an AI agent for students. Chatbots tell you what to do; this one actually does it: files, a task database, web APIs and shell commands, all in a private sandbox." |
| 0:15–0:45 | Click **Turn my notes into study tasks** | "I ask in plain English. The LLM, Llama 3.3 on Groq, plans the work as JSON tool calls. You can see its plan, then each action: it read my notes file, then added one task per topic into a SQLite database. The tasks show up on the right." |
| 0:45–1:00 | Click **Weather for my commute** | "Here it calls an external weather API and reasons about the result." |
| 1:00–1:20 | Click **Delete a file (asks first)**, then **Approve** | "Destructive actions are never run automatically. The agent pauses, and I approve or reject." |
| 1:20–1:35 | Type `run cat ../../etc/passwd` | "Every LLM output is validated before execution. Path escapes, unknown tools and dangerous commands are blocked, and the model is told why." |
| 1:35–1:50 | Open the **Activity log** tab | "Every request, LLM call, token count, action and approval is logged for auditing, and can be downloaded." |
| 1:50–2:00 | Back to chat | "Prompt techniques: a structured JSON contract, ReAct loop, few-shot examples, context injection and self-correction. Code is on GitHub. Thanks!" |

Tip: if the free model is slow or rate limited during recording, the UI shows "Rate limited... retrying". Just let it finish, or cut that part.
