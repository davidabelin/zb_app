# OpenAI SDK / Responses / Conversations / Fine-Tuning — Summary Notes

## 1) My questions

### A. Responses tools in the OpenAI SDK
I asked for a walkthrough of how to use the **Responses API tools** in the OpenAI SDK, especially what kinds of tools exist and how they work in practice.

### B. Conversations API vs Responses API
I asked what is **distinct** about the **Conversations API** compared to the **Responses API**, and whether Conversations is just another name for Responses state or something meaningfully different.

### C. Fine-tuning vs Responses/Conversations
I asked whether **fine-tuning** is effectively being deprecated or replaced by **Responses** and **Conversations**, and what the real differences are in terms of **model behavior** and actual **training options**.

---

## 2) What I found

### A. What the Responses API is
The **Responses API** is OpenAI’s main modern interface for generating model outputs. It supports multimodal input, tool use, stateful interactions, and function calling. In practical terms, it is the runtime engine that produces the model’s next response.

Source: https://developers.openai.com/api/reference/responses/overview/

### B. What “Responses tools” means
Using tools with the Responses API mainly falls into a few buckets:

- **Built-in hosted tools**, such as web search, file search, and code interpreter.
- **Function tools**, where the model emits a tool call and my application executes the function.
- **Custom tools**, where tool input is freer-form rather than tightly structured.
- **MCP / connector-style tools**, where the model can interact with external systems through the platform’s tool framework.

Source: https://developers.openai.com/api/reference/responses/overview/

### C. The basic Responses tool-calling pattern
The standard loop is:

1. Call `client.responses.create(...)`
2. Inspect the output for a tool/function call
3. Execute the tool in my own code if needed
4. Send the tool result back
5. Call `responses.create(...)` again to get the final answer

That is the core runtime pattern for function-style tool use.

Source: https://developers.openai.com/api/docs/guides/function-calling

---

## 3) Responses API vs Conversations API

### A. Responses is the generation engine
The **Responses API** is what actually runs the model and returns output. It is the thing that produces the next answer, performs tool use, and handles inference-time orchestration.

Source: https://developers.openai.com/api/reference/responses/overview/

### B. Conversations is durable server-side state
The **Conversations API** is different: it provides a **durable conversation object** that stores items such as messages, tool calls, tool outputs, and other conversation state over time.

In plain English:

- **Responses** = do the next model step
- **Conversations** = give those steps a persistent home

Source: https://developers.openai.com/api/docs/guides/conversation-state

### C. How Conversations differs from `previous_response_id`
There are two different ways to carry state forward with Responses:

1. **`previous_response_id`** — lightweight chaining from one response to the next
2. **`conversation=...`** — attach the response to a durable conversation object managed by the Conversations API

OpenAI documents these as distinct mechanisms and says they cannot be used together in the same request.

Source: https://developers.openai.com/api/docs/guides/conversation-state

### D. Practical meaning
If I only need short-lived continuity, `previous_response_id` may be enough.

If I want a persistent thread that can be reused across sessions, devices, workers, or app components, the **Conversations API** is the more distinctive and powerful option.

Source: https://developers.openai.com/api/docs/guides/conversation-state

---

## 4) Fine-tuning vs Responses/Conversations

### A. The main correction
My initial thought that “Fine Tuning tools will be deprecated soon, with Responses and Conversations replacing them” turned out to be **mostly incorrect**.

What OpenAI has clearly deprecated is the **Assistants API**, not fine-tuning itself.

OpenAI says the Assistants API is deprecated and scheduled for shutdown on **August 26, 2026**.

Source: https://developers.openai.com/api/docs/assistants/migration
Source: https://developers.openai.com/api/docs/deprecations

### B. Fine-tuning is still supported
Fine-tuning remains a current and supported OpenAI capability. The older legacy `/v1/fine-tunes` endpoint was deprecated in the past, but the current fine-tuning system uses `/v1/fine_tuning/jobs`.

So:
- **Old fine-tunes endpoint**: deprecated
- **Modern fine-tuning system**: still active and supported

Source: https://developers.openai.com/api/reference/fine-tuning/create-job

### C. Core distinction in one sentence
- **Responses / Conversations** change model behavior at **runtime** through prompts, tools, retrieved context, and durable conversation state.
- **Fine-tuning** changes behavior by producing a **trained derivative model** based on examples or feedback.

That is the central difference.

### D. What Responses/Conversations can replace
Responses + Conversations + tools can replace many older reasons people used to reach for custom orchestration or even premature fine-tuning, including:

- persistent thread memory
- tool usage
- retrieval over external knowledge
- workflow state
- some kinds of repeated instruction/persona management

These are now better handled by the modern runtime stack.

Source: https://developers.openai.com/api/docs/guides/migrate-to-responses

### E. What fine-tuning still uniquely does
Fine-tuning still matters when I want the model itself to become more reliable in a particular pattern of behavior.

OpenAI’s guidance frames this roughly as:

- **RAG / retrieval** improves **accuracy** by supplying relevant information
- **Fine-tuning** improves **behavioral consistency** and task specialization

That means fine-tuning is still the right lever when I want:

- more consistent formatting
- more reliable tone/style adherence
- stronger classification behavior
- better performance on a repeated specialized task
- preference learning beyond what prompting alone can robustly achieve

Source: https://developers.openai.com/api/docs/guides/model-selection
Source: https://developers.openai.com/api/docs/guides/model-optimization

---

## 5) Current fine-tuning methods OpenAI documents

OpenAI currently documents multiple fine-tuning methods:

### A. Supervised Fine-Tuning (SFT)
Train on examples of input → desired output.

Useful for:
- structured output behavior
- classification
- repeated transformation tasks
- better instruction following

Source: https://developers.openai.com/api/docs/guides/supervised-fine-tuning

### B. Vision Fine-Tuning
Fine-tuning that includes image inputs to improve image understanding behavior.

Source: https://developers.openai.com/api/docs/guides/vision-fine-tuning/

### C. Direct Preference Optimization (DPO)
Train using preference pairs (preferred response vs less-preferred response).

Useful for:
- style/tone preferences
- subjective output quality preferences
- “pick the better answer” style learning

Source: https://developers.openai.com/api/docs/guides/direct-preference-optimization

### D. Reinforcement Fine-Tuning (RFT)
Train with a feedback / grader signal rather than only fixed gold outputs.

Useful for:
- more complex reasoning behavior
- tasks where success can be graded
- iterative improvement beyond plain supervised examples

Source: https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning

---

## 6) The clean mental model

### Responses
Use when I want:
- generation
- tools
- runtime orchestration
- stateful turns
- multimodal input/output
- agent behavior during inference

### Conversations
Use when I want:
- durable server-side thread state
- persistent items/messages/tool outputs
- a reusable conversation object

### Fine-tuning
Use when I want:
- a model that has actually **learned** a behavioral pattern
- more stable formatting/style/task consistency
- less dependence on repeated prompting
- specialized task behavior embedded into the model itself

---

## 7) Bottom line

The right summary is:

- **Responses/Conversations replace Assistants-style orchestration**
- **Fine-tuning still exists and is not being replaced by Responses**
- **Responses/Conversations = runtime steering**
- **Fine-tuning = learned behavioral adaptation**

So yes, there is some overlap in what problems they can solve, but they are fundamentally different mechanisms.

---

## 8) Practical rules of thumb

Start with:

1. **Prompting / system design**
2. **Responses API**
3. **Tools / retrieval / Conversations**
4. **Evals**
5. **Fine-tuning for fine-tuning**

That appears to match OpenAI’s current guidance as of April 9, 2026: use runtime methods first, and use fine-tuning when you need stronger consistency or specialized learned behavior.

Source: https://developers.openai.com/api/docs/guides/model-optimization