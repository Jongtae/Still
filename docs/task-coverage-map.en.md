## Task coverage map

Issue [#462](https://github.com/Jongtae/Still/issues/462). A map of everyday and work activities, the evidence we have, and the Still blockers to remove.

As of 2026-11-14; evidence read at revision `6d542ab`.
This is not a score: counts are rows in the map, never measures of task success.

- `observed-live`: a dated live observation is cited.
- `fixture-only`: unit or model-free integration evidence, without a claim of live success.
- `blocked-by-still`: Still blocks the task; the work list below names the boundary.
- `engine-limit`: the cited limitation belongs to the owner's AI.
- `out-of-bounds-by-design`: an explicit scope boundary, explained in the map.
- `untried`: no Still execution evidence; specification links may describe a planned case.

Evidence classes: **U** unit/fixture, model-free; **I** model-free integration through a real boundary; **S** static/specification; **P** live public read, no account; **L** live model or live account. S links describe specifications, not recorded runs. Untried and out-of-bounds rows may cite only S scenario, judgment-case or doc links.

Regenerate from the repository root (no live runs):

```sh
PYTHONPATH=evals:src .venv/bin/python -m agentos_eval coverage --write docs/task-coverage-map.en.md
```

### Taxonomies and attribution

- **atus-2025** (adopted): American Time Use Survey Activity Lexicon. Version 2025. [Source](https://www.bls.gov/tus/lexicons/lexiconnoex2025.pdf). U.S. Government work, public domain in the United States. American Time Use Survey Activity Lexicon, 2025, U.S. Bureau of Labor Statistics. Changes: Second-tier identifiers and titles transcribed from the supplied PDF text; n.e.c. second-tier rows and data codes omitted. Still adds task descriptions, needs, status and evidence.
- **onet-31.0-gwa** (adopted): O*NET Content Model Reference: Generalized Work Activities. Version 31.0. [Source](https://www.onetcenter.org/dl_files/database/db_31_0_csv/content_model_reference.csv). CC BY 4.0. O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA. Changes: Only Generalized Work Activity element IDs and names are used; Still adds its own status, needs and evidence columns.
- **kostat-time-use** (considered-not-adopted-yet): Korean Time Use Survey activity classification (생활시간조사). Deferred until an exact public release and its reuse terms are pinned; this offline pass has no Korean classification source.

### Tier-1 groups by status

| Taxonomy | Group | observed-live | fixture-only | blocked-by-still | engine-limit | out-of-bounds-by-design | untried |
| --- | --- | --- | --- | --- | --- | --- | --- |
| atus-2025 | 01 Personal Care | 0 | 0 | 0 | 0 | 5 | 0 |
| atus-2025 | 02 Household Activities | 0 | 1 | 0 | 0 | 8 | 0 |
| atus-2025 | 03 Caring For & Helping Household (HH) Members | 0 | 0 | 0 | 0 | 0 | 5 |
| atus-2025 | 04 Caring For & Helping Nonhousehold (NonHH) Members | 0 | 0 | 0 | 0 | 0 | 5 |
| atus-2025 | 05 Work & Work Related Activities | 0 | 1 | 0 | 0 | 0 | 3 |
| atus-2025 | 06 Education | 0 | 0 | 0 | 0 | 0 | 4 |
| atus-2025 | 07 Consumer Purchases | 0 | 2 | 0 | 0 | 1 | 0 |
| atus-2025 | 08 Professional & Personal Care Services | 0 | 1 | 1 | 0 | 1 | 5 |
| atus-2025 | 09 Household Services | 0 | 0 | 0 | 0 | 0 | 5 |
| atus-2025 | 10 Government Services & Civic Obligations | 0 | 0 | 0 | 0 | 1 | 3 |
| atus-2025 | 11 Eating and Drinking | 0 | 0 | 0 | 0 | 2 | 0 |
| atus-2025 | 12 Socializing, Relaxing, and Leisure | 0 | 0 | 0 | 0 | 1 | 4 |
| atus-2025 | 13 Sports, Exercise, and Recreation | 0 | 0 | 0 | 0 | 3 | 1 |
| atus-2025 | 14 Religious and Spiritual Activities | 0 | 0 | 0 | 0 | 1 | 0 |
| atus-2025 | 15 Volunteer Activities | 0 | 0 | 0 | 0 | 3 | 5 |
| atus-2025 | 16 Telephone Calls | 0 | 0 | 0 | 0 | 0 | 2 |
| atus-2025 | 18 Traveling | 0 | 1 | 0 | 0 | 1 | 15 |
| onet-31.0-gwa | 4.A.1.a Looking for and Receiving Job-Related Information | 0 | 1 | 1 | 0 | 0 | 0 |
| onet-31.0-gwa | 4.A.1.b Identifying and Evaluating Job-Relevant Information | 0 | 0 | 0 | 0 | 0 | 3 |
| onet-31.0-gwa | 4.A.2.a Information and Data Processing | 0 | 0 | 0 | 0 | 0 | 4 |
| onet-31.0-gwa | 4.A.2.b Reasoning and Decision Making | 0 | 1 | 1 | 0 | 0 | 4 |
| onet-31.0-gwa | 4.A.3.a Performing Physical and Manual Work Activities | 0 | 0 | 0 | 0 | 4 | 0 |
| onet-31.0-gwa | 4.A.3.b Performing Complex and Technical Activities | 0 | 2 | 0 | 0 | 2 | 1 |
| onet-31.0-gwa | 4.A.4.a Communicating and Interacting | 0 | 0 | 0 | 0 | 0 | 8 |
| onet-31.0-gwa | 4.A.4.b Coordinating, Developing, Managing, and Advising | 0 | 0 | 0 | 0 | 0 | 6 |
| onet-31.0-gwa | 4.A.4.c Administering | 0 | 1 | 1 | 0 | 0 | 1 |

### Evidence inventory

| Row | Inventory item | S specification links (not runs) | U/I/P/L evidence | Status |
| --- | --- | --- | --- | --- |
| atus-2025:0101 | Sleeping: Help me set a bedtime reminder, while sleeping remains my own activity. | none | none | out-of-bounds-by-design |
| atus-2025:0102 | Grooming: Help me plan a grooming routine, while washing and dressing remain physical tasks. | none | none | out-of-bounds-by-design |
| atus-2025:0103 | Health-related Self Care: Help me keep track of self-care instructions, while I perform the care. | none | none | out-of-bounds-by-design |
| atus-2025:0104 | Personal Activities: Help me reserve private time, while the personal activity remains mine. | none | none | out-of-bounds-by-design |
| atus-2025:0105 | Personal Care Emergencies: Help me find emergency contact information, while a person handles the emergency. | none | none | out-of-bounds-by-design |
| atus-2025:0201 | Housework: Remind me about housework, while a person does the cleaning. | S: [scenario:relative-time-reminder](../evals/scenarios/reminders.json) — Synthetic scenario specification only: Short relative-time reminder. No dated run is claimed. (2026-11-14, `6ba8657`) | none | out-of-bounds-by-design |
| atus-2025:0202 | Food & Drink Prep., Presentation, & Clean-up: Help me plan food preparation, while a person cooks and cleans up. | S: [scenario:allergy-then-meal-plan](../evals/scenarios/memory.json) — Synthetic scenario specification only: Allergy stated after a meal plan request. No dated run is claimed. (2026-11-14, `6ba8657`) | none | out-of-bounds-by-design |
| atus-2025:0203 | Interior Maintenance, Repair, & Decoration: Find instructions for an interior repair, while a person performs it. | none | none | out-of-bounds-by-design |
| atus-2025:0204 | Exterior Maintenance, Repair, & Decoration: Find instructions for an exterior repair, while a person performs it. | none | none | out-of-bounds-by-design |
| atus-2025:0205 | Lawn, Garden, and Houseplants: Find plant-care information, while a person tends the plants. | S: [judgment:followup-topic-ko](../evals/judgments/cases-v1.json) — Synthetic followup_relation input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:followup-topic-en](../evals/judgments/cases-v1.json) — Synthetic followup_relation input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:steer-unrelated-ko](../evals/judgments/cases-v1.json) — Synthetic steers_running_work input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:steer-unrelated-en](../evals/judgments/cases-v1.json) — Synthetic steers_running_work input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | none | out-of-bounds-by-design |
| atus-2025:0206 | Animals and Pets: Help me plan pet care, while a person feeds and exercises the animal. | none | none | out-of-bounds-by-design |
| atus-2025:0207 | Vehicles: Find vehicle maintenance instructions, while a person performs the work. | none | none | out-of-bounds-by-design |
| atus-2025:0208 | Appliances, Tools, and Toys: Find appliance maintenance instructions, while a person performs the repair. | none | none | out-of-bounds-by-design |
| atus-2025:0209 | Household Management: Organize household records and reminders from the information I provide. | S: [scenario:allergy-then-meal-plan](../evals/scenarios/memory.json) — Synthetic scenario specification only: Allergy stated after a meal plan request. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:preference-then-recommendation](../evals/scenarios/memory.json) — Synthetic scenario specification only: Uses a preference stated earlier in the conversation. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:workplace-statement-remember](../evals/scenarios/memory.json) — Synthetic scenario specification only: A stated new workplace to remember. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-correct-size](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: correct size. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-parcel-again](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: parcel again. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-correct-printer](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: correct printer. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:world-appworld-note-update](../evals/scenarios/world-appworld.json) — Synthetic scenario specification only: Write into the owner's own notes (M3a). No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:followup-correction-ko](../evals/judgments/cases-v1.json) — Synthetic followup_relation input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:followup-correction-en](../evals/judgments/cases-v1.json) — Synthetic followup_relation input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:steer-correction-ko](../evals/judgments/cases-v1.json) — Synthetic steers_running_work input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:steer-correction-en](../evals/judgments/cases-v1.json) — Synthetic steers_running_work input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | U: [tests/test_preparations.py::ReminderTests::test_a_reminder_from_the_request_is_delivered_once_and_survives_restart](../tests/test_preparations.py) — A scripted dental-appointment reminder is scheduled and delivered once after a restart with an injected clock and fake Telegram. (2026-11-14, `6ba8657`) | fixture-only |
| atus-2025:0301 | Caring For & Helping HH Children: Help me plan care for a child in my household. | none | none | untried |
| atus-2025:0302 | Activities Related to HH Children's Education: Help me organize a household child's schoolwork and school messages. | none | none | untried |
| atus-2025:0303 | Activities Related to HH Children's Health: Help me organize a household child's health appointments. | none | none | untried |
| atus-2025:0304 | Caring for Household Adults: Help me organize care for an adult in my household. | none | none | untried |
| atus-2025:0305 | Helping Household Adults: Help me organize practical assistance for an adult in my household. | none | none | untried |
| atus-2025:0401 | Caring For & Helping NonHH Children: Help me plan care for a child outside my household. | none | none | untried |
| atus-2025:0402 | Activities Related to Nonhh Children's Education: Help me organize school-related assistance for a child outside my household. | none | none | untried |
| atus-2025:0403 | Activities Related to Nonhh Children's Health: Help me organize health appointments for a child outside my household. | none | none | untried |
| atus-2025:0404 | Caring for Nonhousehold Adults: Help me organize care for an adult outside my household. | none | none | untried |
| atus-2025:0405 | Helping Nonhousehold Adults: Help me organize practical assistance for an adult outside my household. | none | none | untried |
| atus-2025:0501 | Working: Read my work documents and save a source-linked summary. | S: [scenario:recall-dated-studio](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: dated studio. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-quiet-desk](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: quiet desk. No dated run is claimed. (2026-11-14, `6ba8657`) | U: [tests/test_file_workspace_worker_tools.py::WorkerFileWorkspaceTests::test_the_worker_finds_reads_and_saves_a_result_citing_what_it_read](../tests/test_file_workspace_worker_tools.py) — Temporary business-trip documents are found, read and summarized into a new file with source provenance; content is supplied by the fixture, not a live model. (2026-11-14, `6ba8657`) | fixture-only |
| atus-2025:0502 | Work-Related Activities: Help me organize a work-related event. | S: [scenario:recall-hall-again](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: hall again. No dated run is claimed. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:0503 | Other Income-generating Activities: Help me compare options for an income-generating activity. | none | none | untried |
| atus-2025:0504 | Job Search and Interviewing: Find job openings and help me prepare an application. | none | none | untried |
| atus-2025:0601 | Taking Class: Help me organize materials for a class I attend. | none | none | untried |
| atus-2025:0602 | Extracurricular School Activities (Except Sports): Help me plan an extracurricular school activity. | none | none | untried |
| atus-2025:0603 | Research/Homework: Help me research a homework question and cite the sources. | none | none | untried |
| atus-2025:0604 | Registration/Administrative activities: Help me find course registration requirements and prepare the information needed. | none | none | untried |
| atus-2025:0701 | Shopping (Store, Telephone, Internet): Put a requested product in a shopping cart and read back the result. | S: [scenario:skill-add-reference-by-name](../evals/scenarios/skills.json) — Synthetic scenario specification only: Owner asks for the shopping skill by name on a fresh install. No dated run is claimed. (2026-11-14, `6ba8657`) | U: [tests/test_skill_shop.py::FirstUseOnAFreshStore::test_supplied_knowledge_is_used_on_the_first_request_with_one_change_and_a_readback](../tests/test_skill_shop.py) — Scripted skill loads and a fixture browser add one product, then read the cart back; no real store or payment. (2026-11-14, `6ba8657`)<br>L: [docs/skill-eval-01-coverage.en.md#5-live-sample-results](../docs/skill-eval-01-coverage.en.md#5-live-sample-results) — Run 1 stopped at sign-in after loading two skills; zero cart mutations, later cases not run. This is a failed live attempt, not live cart completion. (2026-10-03, `efb5977`) | fixture-only |
| atus-2025:0702 | Researching Purchases: Compare products, prices and purchasing constraints with sources. | S: [scenario:line-change-stock-time](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: change stock time. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:shopping-followup-constraint](../evals/scenarios/shopping.json) — Synthetic scenario specification only: Shopping follow-up adds a constraint. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:shopping-lookup-compare](../evals/scenarios/shopping.json) — Synthetic scenario specification only: Shopping lookup with prices and where to buy. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:new-request-after-lunch-topic](../evals/scenarios/topic-shift.json) — Synthetic scenario specification only: Unrelated new request after a lunch topic. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:new-request-after-travel-topic](../evals/scenarios/topic-shift.json) — Synthetic scenario specification only: Unrelated new request after a travel topic. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:standing-wish-is-not-a-memory](../evals/scenarios/watches.json) — Synthetic scenario specification only: The request sentence of a standing wish is not saved as a memory. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:open-question-true-ko](../evals/judgments/cases-v1.json) — Synthetic answers_open_question input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:open-question-true-en](../evals/judgments/cases-v1.json) — Synthetic answers_open_question input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-partial](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-partial-ko](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:escalation-en](../evals/judgments/cases-v1.json) — Synthetic orchestration_escalation input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:escalation-ko](../evals/judgments/cases-v1.json) — Synthetic orchestration_escalation input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | U: [tests/test_presence_eval_matrix.py::G_LongResearch::test_long_generation_is_one_stop_able_draft_then_one_durable_answer](../tests/test_presence_eval_matrix.py) — Scripted headphone research reads two fake public pages and delivers one sourced reply; no live comparison quality claim. (2026-11-14, `6ba8657`) | fixture-only |
| atus-2025:0703 | Security Procedures Rel. to Consumer Purchases: Explain a purchase security procedure while I complete the identity check. | none | none | out-of-bounds-by-design |
| atus-2025:0801 | Childcare Services: Find childcare services and help me arrange an appointment. | none | none | untried |
| atus-2025:0802 | Financial Services and Banking: Help me review banking information and prepare an explicitly approved payment. | S: [scenario:world-appworld-pay-denied](../evals/scenarios/world-appworld.json) — Synthetic scenario specification only: Do not pay after the owner declines (M3c/e). No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:world-appworld-pay-approved](../evals/scenarios/world-appworld.json) — Synthetic scenario specification only: Pay someone after the owner approves (M3c/e). No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:effect-payment](../evals/judgments/cases-v1.json) — Synthetic connector_effect input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [src/personal_agent/quickstart_service.py#L5495](../src/personal_agent/quickstart_service.py#L5495) — Static inspection of AgentService.attempt_observations: the CLI completion check receives serialized evidence metadata, not the tool result body; open #767 records valid connected-account reads judged unsupported. (2026-11-14, `6d542ab`)<br>S: [src/personal_agent/code_run.py#L1069](../src/personal_agent/code_run.py#L1069) — Static inspection of evidence: persisted code_run evidence excludes stdout and request bodies, including the read fields needed to substantiate balance/order claims in #767; not a new execution. (2026-11-14, `6d542ab`) | none | blocked-by-still |
| atus-2025:0803 | Legal Services: Help me find legal services and organize questions for the provider. | none | none | untried |
| atus-2025:0804 | Medical and Care Services: Remind me about a medical appointment that I already arranged. | none | U: [tests/test_preparations.py::ReminderTests::test_a_reminder_from_the_request_is_delivered_once_and_survives_restart](../tests/test_preparations.py) — A scripted dental-appointment reminder is scheduled and delivered once after a restart with an injected clock and fake Telegram. (2026-11-14, `6ba8657`) | fixture-only |
| atus-2025:0805 | Personal Care Services: Find personal care services and help me arrange an appointment. | none | none | untried |
| atus-2025:0806 | Real Estate: Help me compare real estate listings and organize enquiries. | none | none | untried |
| atus-2025:0807 | Veterinary Services (excluding grooming): Find veterinary services and help me arrange an appointment. | none | none | untried |
| atus-2025:0808 | Security Procedures Rel. to Professional/Personal Svcs.: Explain a service security procedure while I complete the identity check. | none | none | out-of-bounds-by-design |
| atus-2025:0901 | Household Services (not done by self): Help me find a household service provider. | none | none | untried |
| atus-2025:0902 | Home Maint/Repair/Décor/Construction (not done by self): Help me compare contractors for home maintenance. | none | none | untried |
| atus-2025:0903 | Pet Services (not done by self, not vet): Help me find pet care services. | none | none | untried |
| atus-2025:0904 | Lawn & Garden Services (not done by self): Help me compare lawn and garden service providers. | none | none | untried |
| atus-2025:0905 | Vehicle Maint. & Repair Services (not done by self): Help me find a vehicle repair service. | none | none | untried |
| atus-2025:1001 | Using Government Services: Find the requirements for a government service and help me prepare an application. | none | none | untried |
| atus-2025:1002 | Civic Obligations & Participation: Help me find information about a civic obligation or meeting. | none | none | untried |
| atus-2025:1003 | Waiting Associated w/Govt Svcs or Civic Obligations: Help me check an expected wait for a government service. | none | none | untried |
| atus-2025:1004 | Security Procedures Rel. to Govt Svcs/Civic Obligations: Explain a government service security procedure while I complete it. | none | none | out-of-bounds-by-design |
| atus-2025:1101 | Eating and Drinking: Suggest a meal, while eating and drinking remain physical activities. | S: [scenario:already-ate-dinner-next-meal](../evals/scenarios/food.json) — Synthetic scenario specification only: Already ate dinner, plan the next meal without repeating. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:already-ate-lunch](../evals/scenarios/food.json) — Synthetic scenario specification only: 'I already ate' after a lunch recommendation. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:weekday-lunch-near-workplace](../evals/scenarios/food.json) — Synthetic scenario specification only: Weekday lunch near a stated workplace. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:food-on-route-budget-correction](../evals/scenarios/food.json) — Synthetic scenario specification only: Rest-area recommendation corrected by a later budget. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:food-on-route-rest-area](../evals/scenarios/food.json) — Synthetic scenario specification only: Rest-area food on a stated route, with menu and price. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:preference-then-recommendation](../evals/scenarios/memory.json) — Synthetic scenario specification only: Uses a preference stated earlier in the conversation. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:new-request-after-lunch-topic](../evals/scenarios/topic-shift.json) — Synthetic scenario specification only: Unrelated new request after a lunch topic. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:standing-wish-becomes-watch](../evals/scenarios/watches.json) — Synthetic scenario specification only: A standing wish after a result becomes a proposed watch. No dated run is claimed. (2026-11-14, `6ba8657`) | none | out-of-bounds-by-design |
| atus-2025:1102 | Waiting associated with eating & drinking: Help me check a dining wait time, while a person waits at the venue. | none | none | out-of-bounds-by-design |
| atus-2025:1201 | Socializing and Communicating: Help me draft a personal message or respond in conversation. | S: [scenario:presence-chat-short-reply-stays-short](../evals/scenarios/presence.json) — Synthetic scenario specification only: Paired chat: a short exchange gets one reply and no preview. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-dated-committee](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: dated committee. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:world-appworld-send-text](../evals/scenarios/world-appworld.json) — Synthetic scenario specification only: Send a message on the owner's behalf (M3b). No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:owner-act-approve](../evals/judgments/cases-v1.json) — Synthetic owner_act_answer input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:owner-act-decline](../evals/judgments/cases-v1.json) — Synthetic owner_act_answer input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:owner-act-none](../evals/judgments/cases-v1.json) — Synthetic owner_act_answer input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-unknown-ko](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:1202 | Attending or Hosting Social Events: Help me plan a social event and its invitations. | none | none | untried |
| atus-2025:1203 | Relaxing and Leisure: Help me find leisure reading or a hobby activity. | S: [scenario:line-past-submission-deadline](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: past submission deadline. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:line-status-before-first-run](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: status before first run. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:line-cancel-watch](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: cancel watch. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:line-change-watch-time](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: change watch time. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:line-evening-watch](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: evening watch. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-forget-project](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: forget project. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-clay-language](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: clay language. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-irrelevant-music](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: irrelevant music. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-expired-pass](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: expired pass. No dated run is claimed. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:1204 | Arts and Entertainment (other than sports): Help me find an exhibition or performance and plan a visit. | S: [scenario:line-cancel-seat-watch](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: cancel seat watch. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:line-add-watch-rule](../evals/scenarios/continuity-experiments.json) — Synthetic scenario specification only: add watch rule. No dated run is claimed. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:1205 | Waiting Associated with Socializing, Relaxing, and Leisure: Help me check a social event wait time, while a person waits at the venue. | none | none | out-of-bounds-by-design |
| atus-2025:1301 | Participating in Sports, Exercise, or Recreation: Help me plan exercise or a sports reminder, while a person does the activity. | S: [scenario:correct-earlier-advice-hiking](../evals/scenarios/corrections.json) — Synthetic scenario specification only: Correct earlier advice after a new personal fact. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-forget-route](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: forget route. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:tee-time-reminder](../evals/scenarios/reminders.json) — Synthetic scenario specification only: Reminder the night before a stated tee time. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:plan-schedule](../evals/judgments/cases-v1.json) — Synthetic orchestration_plan input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | none | out-of-bounds-by-design |
| atus-2025:1302 | Attending Sporting/Recreational Events: Help me find a sporting event to attend. | none | none | untried |
| atus-2025:1303 | Waiting Associated with Sports, Exercise, & Recreation: Help me check an event wait time, while a person waits at the venue. | none | none | out-of-bounds-by-design |
| atus-2025:1304 | Security Procedures Rel. to Sports, Exercise, & Recreation: Explain an event security procedure while I complete it. | none | none | out-of-bounds-by-design |
| atus-2025:1401 | Religious/Spiritual Practices: Help me find information for a religious or spiritual practice I undertake. | none | none | out-of-bounds-by-design |
| atus-2025:1501 | Administrative & Support Activities: Help me organize records and communications for volunteer work. | none | none | untried |
| atus-2025:1502 | Social Service & Care Activities (Except Medical): Help me organize a volunteer care activity. | none | none | untried |
| atus-2025:1503 | Indoor & Outdoor Maintenance, Building, & Clean-up Activities: Help me plan volunteer maintenance work, while people perform it. | none | none | out-of-bounds-by-design |
| atus-2025:1504 | Participating in Performance & Cultural Activities: Help me prepare material for a volunteer cultural performance. | none | none | untried |
| atus-2025:1505 | Attending Meetings, Conferences, & Training: Help me organize attendance at volunteer meetings or training. | none | none | untried |
| atus-2025:1506 | Public Health & Safety Activities: Help me find information for a volunteer public health activity. | none | none | untried |
| atus-2025:1507 | Waiting Associated with Volunteer Activities: Help me check a volunteer activity wait time, while a person waits there. | none | none | out-of-bounds-by-design |
| atus-2025:1508 | Security Procedures related to volunteer activities: Explain a volunteer security procedure while I complete it. | none | none | out-of-bounds-by-design |
| atus-2025:1601 | Telephone Calls (to or from): Help me place a telephone call and relay my message. | none | none | untried |
| atus-2025:1602 | Waiting Associated with Telephone Calls: Help me keep track of a telephone queue or callback. | none | none | untried |
| atus-2025:1801 | Travel Related to Personal Care: Help me plan travel for personal care. | none | none | untried |
| atus-2025:1802 | Travel Related to Household Activities: Help me plan travel for household activities. | none | none | untried |
| atus-2025:1803 | Travel Related to Caring For & Helping HH Members: Help me plan travel to care for a household member. | none | none | untried |
| atus-2025:1804 | Travel Related to Caring For & Helping Nonhh Members: Help me plan travel to care for someone outside my household. | none | none | untried |
| atus-2025:1805 | Travel Related to Work: Help me organize a business trip or commute. | S: [scenario:presence-chat-lookup-with-a-judgment](../evals/scenarios/presence.json) — Synthetic scenario specification only: Paired chat: a lookup that ends in a recommendation. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:settings-change-with-confirmation](../evals/scenarios/settings.json) — Synthetic scenario specification only: Change a setting in conversation with confirm-before-apply. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:travel-time-with-clock-note](../evals/scenarios/travel.json) — Synthetic scenario specification only: Will I be late - travel time against a stated appointment. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:travel-time-after-event](../evals/scenarios/travel.json) — Synthetic scenario specification only: Train time after an earlier-stated event. No dated run is claimed. (2026-11-14, `6ba8657`) | U: [tests/test_file_workspace_worker_tools.py::WorkerFileWorkspaceTests::test_the_worker_finds_reads_and_saves_a_result_citing_what_it_read](../tests/test_file_workspace_worker_tools.py) — Temporary business-trip documents are found, read and summarized into a new file with source provenance; content is supplied by the fixture, not a live model. (2026-11-14, `6ba8657`) | fixture-only |
| atus-2025:1806 | Travel Related to Education: Help me plan travel to school or a class. | none | none | untried |
| atus-2025:1807 | Travel Related to Consumer Purchases: Help me plan travel for shopping. | none | none | untried |
| atus-2025:1808 | Travel Related to Using Professional and Personal Care Services: Help me plan travel to a professional or personal service. | none | none | untried |
| atus-2025:1809 | Travel Related to Using Household Services: Help me plan travel to a household service provider. | none | none | untried |
| atus-2025:1810 | Travel Related to Using Govt Services & Civic Obligations: Help me plan travel to a government service or civic activity. | none | none | untried |
| atus-2025:1811 | Travel Related to Eating and Drinking: Help me plan a route to a meal stop. | S: [scenario:food-on-route-budget-correction](../evals/scenarios/food.json) — Synthetic scenario specification only: Rest-area recommendation corrected by a later budget. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:food-on-route-rest-area](../evals/scenarios/food.json) — Synthetic scenario specification only: Rest-area food on a stated route, with menu and price. No dated run is claimed. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:1812 | Travel Related to Socializing, Relaxing, and Leisure: Help me plan travel for a social or leisure visit. | S: [scenario:presence-chat-research-takes-a-while](../evals/scenarios/presence.json) — Synthetic scenario specification only: Paired chat: a request that takes a while is taken on like a person would. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:new-request-after-travel-topic](../evals/scenarios/topic-shift.json) — Synthetic scenario specification only: Unrelated new request after a travel topic. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:travel-time-from-earlier-place](../evals/scenarios/travel.json) — Synthetic scenario specification only: Travel time from a place named in the previous turn. No dated run is claimed. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:1813 | Travel Related to Sports, Exercise, & Recreation: Help me plan departure time for a sports outing. | S: [scenario:tee-time-departure-reminder](../evals/scenarios/reminders.json) — Synthetic scenario specification only: Departure reminder worked back from a tee time and home area. No dated run is claimed. (2026-11-14, `6ba8657`) | none | untried |
| atus-2025:1814 | Travel Related to Religious/Spiritual Activities: Help me plan travel to a religious activity. | none | none | untried |
| atus-2025:1815 | Travel Related to Volunteer Activities: Help me plan travel to a volunteer activity. | none | none | untried |
| atus-2025:1816 | Travel Related to Telephone Calls: Help me plan travel associated with making a telephone call. | none | none | untried |
| atus-2025:1818 | Security Procedures Related to Traveling: Explain a travel security procedure while I complete it. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.1.a.1 | Getting Information: Gather source material for a work question. | none | U: [tests/test_file_workspace_worker_tools.py::WorkerFileWorkspaceTests::test_the_worker_finds_reads_and_saves_a_result_citing_what_it_read](../tests/test_file_workspace_worker_tools.py) — Temporary business-trip documents are found, read and summarized into a new file with source provenance; content is supplied by the fixture, not a live model. (2026-11-14, `6ba8657`) | fixture-only |
| onet-31.0-gwa:4.A.1.a.2 | Monitoring Processes, Materials, or Surroundings: Monitor a specified information source for relevant changes. | S: [src/personal_agent/preparations.py#L581](../src/personal_agent/preparations.py#L581) — Static inspection of window_report_reason: a promised report depends on elapsed % report_seconds == 0 for the settled slot, with no overdue-report cursor (#813). (2026-11-14, `6d542ab`)<br>S: [src/personal_agent/preparations.py#L483](../src/personal_agent/preparations.py#L483) — Static inspection of normalize_window: omitted max_runs silently becomes min(slots, MAX_WINDOW_RUNS); schedule_text renders until_at, so the requested end can exceed the last check (#814). (2026-11-14, `6d542ab`)<br>S: [docs/outcome-experiments.en.md#foundations-and-gaps](../docs/outcome-experiments.en.md#foundations-and-gaps) — The existing experiment design records #813 skipped-slot reports and #814 silent run-cap truncation as gaps; their trace steps are skipped, not passing execution evidence. (2026-11-14, `6d542ab`) | none | blocked-by-still |
| onet-31.0-gwa:4.A.1.b.1 | Identifying Objects, Actions, and Events: Identify changes or categories in supplied information. | none | none | untried |
| onet-31.0-gwa:4.A.1.b.2 | Inspecting Equipment, Structures, or Materials: Inspect supplied equipment records for signs of a defect. | none | none | untried |
| onet-31.0-gwa:4.A.1.b.3 | Estimating the Quantifiable Characteristics of Products, Events, or Information: Estimate the time and resources needed for an activity. | none | none | untried |
| onet-31.0-gwa:4.A.2.a.1 | Judging the Qualities of Objects, Services, or People: Assess the quality of alternatives using stated criteria. | none | none | untried |
| onet-31.0-gwa:4.A.2.a.2 | Processing Information: Compile and calculate information from supplied records. | none | none | untried |
| onet-31.0-gwa:4.A.2.a.3 | Evaluating Information to Determine Compliance with Standards: Compare supplied records with an identified standard. | none | none | untried |
| onet-31.0-gwa:4.A.2.a.4 | Analyzing Data or Information: Analyze a dataset and explain the findings. | none | none | untried |
| onet-31.0-gwa:4.A.2.b.1 | Making Decisions and Solving Problems: Compare options and recommend a solution with reasons. | S: [src/personal_agent/quickstart_service.py#L5206](../src/personal_agent/quickstart_service.py#L5206) — Static inspection of AgentService.continued_exchange: the linked Work contributes its request, final reply and sign-in notices, not its comparison tool results; corroborates the missing reasons in open #768. (2026-11-14, `6d542ab`)<br>S: [src/personal_agent/code_run.py#L1069](../src/personal_agent/code_run.py#L1069) — Static inspection of evidence: code_run output is excluded from persisted evidence, so a later Work cannot recover comparison fields from that record (#768). (2026-11-14, `6d542ab`) | none | blocked-by-still |
| onet-31.0-gwa:4.A.2.b.2 | Thinking Creatively: Develop draft ideas for a new design. | none | none | untried |
| onet-31.0-gwa:4.A.2.b.3 | Updating and Using Relevant Knowledge: Find current technical knowledge relevant to my work. | none | none | untried |
| onet-31.0-gwa:4.A.2.b.4 | Developing Objectives and Strategies: Help me draft objectives and a strategy. | none | none | untried |
| onet-31.0-gwa:4.A.2.b.5 | Scheduling Work and Activities: Schedule an activity and remind me when it is due. | none | U: [tests/test_preparations.py::ReminderTests::test_a_reminder_from_the_request_is_delivered_once_and_survives_restart](../tests/test_preparations.py) — A scripted dental-appointment reminder is scheduled and delivered once after a restart with an injected clock and fake Telegram. (2026-11-14, `6ba8657`) | fixture-only |
| onet-31.0-gwa:4.A.2.b.6 | Organizing, Planning, and Prioritizing Work: Help me prioritize and organize a work plan. | none | none | untried |
| onet-31.0-gwa:4.A.3.a.1 | Performing General Physical Activities: Help me plan physical work that a person must carry out. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.3.a.2 | Handling and Moving Objects: Help me plan moving objects that a person must handle. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.3.a.3 | Controlling Machines and Processes: Help me prepare operating instructions for a machine a person controls. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.3.a.4 | Operating Vehicles, Mechanized Devices, or Equipment: Help me plan a vehicle journey that a person must drive. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.3.b.1 | Working with Computers: Run a bounded program or help me configure a computer tool. | S: [scenario:settings-change-with-confirmation](../evals/scenarios/settings.json) — Synthetic scenario specification only: Change a setting in conversation with confirm-before-apply. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:settings-read-in-conversation](../evals/scenarios/settings.json) — Synthetic scenario specification only: Read a setting in conversation. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:skill-add-reference-by-name](../evals/scenarios/skills.json) — Synthetic scenario specification only: Owner asks for the shopping skill by name on a fresh install. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:plan-technical](../evals/judgments/cases-v1.json) — Synthetic orchestration_plan input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:plan-password](../evals/judgments/cases-v1.json) — Synthetic orchestration_plan input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | U: [tests/test_skill_shop.py::OwnerAddsAReferenceSkillByName::test_adding_by_name_pins_the_commit_and_switches_skills_on_once_confirmed](../tests/test_skill_shop.py) — A fake GitHub transport supplies a skill package; settings confirmation pins its revision and enables it. No live install or model-written program is exercised. (2026-11-14, `6ba8657`) | fixture-only |
| onet-31.0-gwa:4.A.3.b.2 | Drafting, Laying Out, and Specifying Technical Devices, Parts, and Equipment: Draft technical instructions or specifications from supplied requirements. | none | none | untried |
| onet-31.0-gwa:4.A.3.b.4 | Repairing and Maintaining Mechanical Equipment: Find maintenance guidance for mechanical equipment a person must repair. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.3.b.5 | Repairing and Maintaining Electronic Equipment: Find maintenance guidance for electronic equipment a person must repair. | none | none | out-of-bounds-by-design |
| onet-31.0-gwa:4.A.3.b.6 | Documenting/Recording Information: Record supplied information in a source-linked file. | S: [scenario:recall-reading-format](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: reading format. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [scenario:recall-binding-language](../evals/scenarios/recall-experiments.json) — Synthetic scenario specification only: binding language. No dated run is claimed. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-failed](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-failed-ko](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-unknown](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | U: [tests/test_file_workspace_worker_tools.py::WorkerFileWorkspaceTests::test_the_worker_finds_reads_and_saves_a_result_citing_what_it_read](../tests/test_file_workspace_worker_tools.py) — Temporary business-trip documents are found, read and summarized into a new file with source provenance; content is supplied by the fixture, not a live model. (2026-11-14, `6ba8657`) | fixture-only |
| onet-31.0-gwa:4.A.4.a.1 | Interpreting the Meaning of Information for Others: Explain supplied findings in language the intended reader can use. | S: [judgment:goal-served](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:goal-served-ko](../evals/judgments/cases-v1.json) — Synthetic goal_reached input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:plan-plain-en](../evals/judgments/cases-v1.json) — Synthetic orchestration_plan input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`)<br>S: [judgment:plan-plain-ko](../evals/judgments/cases-v1.json) — Synthetic orchestration_plan input and expected judgment; not a task execution or a dated model run. (2026-11-14, `6ba8657`) | none | untried |
| onet-31.0-gwa:4.A.4.a.2 | Communicating with Supervisors, Peers, or Subordinates: Draft a work update for a supervisor or colleague. | none | none | untried |
| onet-31.0-gwa:4.A.4.a.3 | Communicating with People Outside the Organization: Draft a message to an external contact for my review. | none | none | untried |
| onet-31.0-gwa:4.A.4.a.4 | Establishing and Maintaining Interpersonal Relationships: Help me prepare for a conversation with a colleague. | none | none | untried |
| onet-31.0-gwa:4.A.4.a.5 | Assisting and Caring for Others: Help me organize assistance for another person. | none | none | untried |
| onet-31.0-gwa:4.A.4.a.6 | Selling or Influencing Others: Draft a persuasive explanation for an intended audience. | none | none | untried |
| onet-31.0-gwa:4.A.4.a.7 | Resolving Conflicts and Negotiating with Others: Help me prepare options for resolving a disagreement. | none | none | untried |
| onet-31.0-gwa:4.A.4.a.8 | Performing for or Working Directly with the Public: Help me prepare information for a public-facing activity. | none | none | untried |
| onet-31.0-gwa:4.A.4.b.1 | Coordinating the Work and Activities of Others: Help me coordinate a group work plan. | none | none | untried |
| onet-31.0-gwa:4.A.4.b.2 | Developing and Building Teams: Help me prepare a team-building activity. | none | none | untried |
| onet-31.0-gwa:4.A.4.b.3 | Training and Teaching Others: Prepare a teaching outline from supplied learning goals. | none | none | untried |
| onet-31.0-gwa:4.A.4.b.4 | Guiding, Directing, and Motivating Subordinates: Help me draft guidance and expectations for a team. | none | none | untried |
| onet-31.0-gwa:4.A.4.b.5 | Coaching and Developing Others: Help me prepare a coaching plan. | none | none | untried |
| onet-31.0-gwa:4.A.4.b.6 | Providing Consultation and Advice to Others: Prepare advice on a technical or process question with sources. | none | none | untried |
| onet-31.0-gwa:4.A.4.c.1 | Performing Administrative Activities: Organize work files and prepare routine paperwork. | none | U: [tests/test_file_workspace_worker_tools.py::WorkerFileWorkspaceTests::test_the_worker_finds_reads_and_saves_a_result_citing_what_it_read](../tests/test_file_workspace_worker_tools.py) — Temporary business-trip documents are found, read and summarized into a new file with source provenance; content is supplied by the fixture, not a live model. (2026-11-14, `6ba8657`) | fixture-only |
| onet-31.0-gwa:4.A.4.c.2 | Staffing Organizational Units: Help me compare applications against stated hiring criteria. | none | none | untried |
| onet-31.0-gwa:4.A.4.c.3 | Monitoring and Controlling Resources: Help me review inventory and spending records. | S: [src/personal_agent/quickstart_service.py#L5495](../src/personal_agent/quickstart_service.py#L5495) — Static inspection of AgentService.attempt_observations: the CLI completion check receives serialized evidence metadata, not the tool result body; open #767 records valid connected-account reads judged unsupported. (2026-11-14, `6d542ab`)<br>S: [src/personal_agent/code_run.py#L1069](../src/personal_agent/code_run.py#L1069) — Static inspection of evidence: persisted code_run evidence excludes stdout and request bodies, including the read fields needed to substantiate balance/order claims in #767; not a new execution. (2026-11-14, `6d542ab`) | none | blocked-by-still |

### Untried

- `atus-2025:0301`: Caring For & Helping HH Children
- `atus-2025:0302`: Activities Related to HH Children's Education
- `atus-2025:0303`: Activities Related to HH Children's Health
- `atus-2025:0304`: Caring for Household Adults
- `atus-2025:0305`: Helping Household Adults
- `atus-2025:0401`: Caring For & Helping NonHH Children
- `atus-2025:0402`: Activities Related to Nonhh Children's Education
- `atus-2025:0403`: Activities Related to Nonhh Children's Health
- `atus-2025:0404`: Caring for Nonhousehold Adults
- `atus-2025:0405`: Helping Nonhousehold Adults
- `atus-2025:0502`: Work-Related Activities
- `atus-2025:0503`: Other Income-generating Activities
- `atus-2025:0504`: Job Search and Interviewing
- `atus-2025:0601`: Taking Class
- `atus-2025:0602`: Extracurricular School Activities (Except Sports)
- `atus-2025:0603`: Research/Homework
- `atus-2025:0604`: Registration/Administrative activities
- `atus-2025:0801`: Childcare Services
- `atus-2025:0803`: Legal Services
- `atus-2025:0805`: Personal Care Services
- `atus-2025:0806`: Real Estate
- `atus-2025:0807`: Veterinary Services (excluding grooming)
- `atus-2025:0901`: Household Services (not done by self)
- `atus-2025:0902`: Home Maint/Repair/Décor/Construction (not done by self)
- `atus-2025:0903`: Pet Services (not done by self, not vet)
- `atus-2025:0904`: Lawn & Garden Services (not done by self)
- `atus-2025:0905`: Vehicle Maint. & Repair Services (not done by self)
- `atus-2025:1001`: Using Government Services
- `atus-2025:1002`: Civic Obligations & Participation
- `atus-2025:1003`: Waiting Associated w/Govt Svcs or Civic Obligations
- `atus-2025:1201`: Socializing and Communicating
- `atus-2025:1202`: Attending or Hosting Social Events
- `atus-2025:1203`: Relaxing and Leisure
- `atus-2025:1204`: Arts and Entertainment (other than sports)
- `atus-2025:1302`: Attending Sporting/Recreational Events
- `atus-2025:1501`: Administrative & Support Activities
- `atus-2025:1502`: Social Service & Care Activities (Except Medical)
- `atus-2025:1504`: Participating in Performance & Cultural Activities
- `atus-2025:1505`: Attending Meetings, Conferences, & Training
- `atus-2025:1506`: Public Health & Safety Activities
- `atus-2025:1601`: Telephone Calls (to or from)
- `atus-2025:1602`: Waiting Associated with Telephone Calls
- `atus-2025:1801`: Travel Related to Personal Care
- `atus-2025:1802`: Travel Related to Household Activities
- `atus-2025:1803`: Travel Related to Caring For & Helping HH Members
- `atus-2025:1804`: Travel Related to Caring For & Helping Nonhh Members
- `atus-2025:1806`: Travel Related to Education
- `atus-2025:1807`: Travel Related to Consumer Purchases
- `atus-2025:1808`: Travel Related to Using Professional and Personal Care Services
- `atus-2025:1809`: Travel Related to Using Household Services
- `atus-2025:1810`: Travel Related to Using Govt Services & Civic Obligations
- `atus-2025:1811`: Travel Related to Eating and Drinking
- `atus-2025:1812`: Travel Related to Socializing, Relaxing, and Leisure
- `atus-2025:1813`: Travel Related to Sports, Exercise, & Recreation
- `atus-2025:1814`: Travel Related to Religious/Spiritual Activities
- `atus-2025:1815`: Travel Related to Volunteer Activities
- `atus-2025:1816`: Travel Related to Telephone Calls
- `onet-31.0-gwa:4.A.1.b.1`: Identifying Objects, Actions, and Events
- `onet-31.0-gwa:4.A.1.b.2`: Inspecting Equipment, Structures, or Materials
- `onet-31.0-gwa:4.A.1.b.3`: Estimating the Quantifiable Characteristics of Products, Events, or Information
- `onet-31.0-gwa:4.A.2.a.1`: Judging the Qualities of Objects, Services, or People
- `onet-31.0-gwa:4.A.2.a.2`: Processing Information
- `onet-31.0-gwa:4.A.2.a.3`: Evaluating Information to Determine Compliance with Standards
- `onet-31.0-gwa:4.A.2.a.4`: Analyzing Data or Information
- `onet-31.0-gwa:4.A.2.b.2`: Thinking Creatively
- `onet-31.0-gwa:4.A.2.b.3`: Updating and Using Relevant Knowledge
- `onet-31.0-gwa:4.A.2.b.4`: Developing Objectives and Strategies
- `onet-31.0-gwa:4.A.2.b.6`: Organizing, Planning, and Prioritizing Work
- `onet-31.0-gwa:4.A.3.b.2`: Drafting, Laying Out, and Specifying Technical Devices, Parts, and Equipment
- `onet-31.0-gwa:4.A.4.a.1`: Interpreting the Meaning of Information for Others
- `onet-31.0-gwa:4.A.4.a.2`: Communicating with Supervisors, Peers, or Subordinates
- `onet-31.0-gwa:4.A.4.a.3`: Communicating with People Outside the Organization
- `onet-31.0-gwa:4.A.4.a.4`: Establishing and Maintaining Interpersonal Relationships
- `onet-31.0-gwa:4.A.4.a.5`: Assisting and Caring for Others
- `onet-31.0-gwa:4.A.4.a.6`: Selling or Influencing Others
- `onet-31.0-gwa:4.A.4.a.7`: Resolving Conflicts and Negotiating with Others
- `onet-31.0-gwa:4.A.4.a.8`: Performing for or Working Directly with the Public
- `onet-31.0-gwa:4.A.4.b.1`: Coordinating the Work and Activities of Others
- `onet-31.0-gwa:4.A.4.b.2`: Developing and Building Teams
- `onet-31.0-gwa:4.A.4.b.3`: Training and Teaching Others
- `onet-31.0-gwa:4.A.4.b.4`: Guiding, Directing, and Motivating Subordinates
- `onet-31.0-gwa:4.A.4.b.5`: Coaching and Developing Others
- `onet-31.0-gwa:4.A.4.b.6`: Providing Consultation and Advice to Others
- `onet-31.0-gwa:4.A.4.c.2`: Staffing Organizational Units

### Still-side blockers

The C16 work list: remove Still blockers so the owner's AI can do the work. Counts are rows only; the same issue can block several rows. Routing includes wrong routing and Still orchestration judgments, such as the completion check.

#### By blocker kind

| Blocker kind | Row count | Rows |
| --- | --- | --- |
| routing | 4 | `atus-2025:0802`, `onet-31.0-gwa:4.A.1.a.2`, `onet-31.0-gwa:4.A.2.b.1`, `onet-31.0-gwa:4.A.4.c.3` |

#### By issue

| Issue | Row count | Rows and blockers | Code references (by row) |
| --- | --- | --- | --- |
| [#767](https://github.com/Jongtae/Still/issues/767) | 2 | `atus-2025:0802`: routing — For banking information read through code_run, Still can judge a supported balance answer unsupported because the completion check lacks the returned fields (#767).<br>`onet-31.0-gwa:4.A.4.c.3`: routing — Reviewing connected-account balance/order records through code_run can end with a contradictory unsupported verdict because Still omits the read fields from completion evidence (#767). | `atus-2025:0802`: `src/personal_agent/quickstart_service.py:AgentService.attempt_observations`<br>`onet-31.0-gwa:4.A.4.c.3`: `src/personal_agent/quickstart_service.py:AgentService.attempt_observations` |
| [#768](https://github.com/Jongtae/Still/issues/768) | 1 | `onet-31.0-gwa:4.A.2.b.1`: routing — When the owner asks why a previous recommendation was chosen, the linked follow-up lacks the earlier code_run comparison results needed to explain it (#768). | `onet-31.0-gwa:4.A.2.b.1`: `src/personal_agent/quickstart_service.py:AgentService.continued_exchange` |
| [#813](https://github.com/Jongtae/Still/issues/813) | 1 | `onet-31.0-gwa:4.A.1.a.2`: routing — A watch loses a promised interval report when a long-running check skips its boundary (#813); a long requested watch also ends at the silent 96-check cap (#814). | `onet-31.0-gwa:4.A.1.a.2`: `src/personal_agent/preparations.py:window_report_reason` |

### Regression links

| Row | Scenarios | Judgment cases |
| --- | --- | --- |
| atus-2025:0101 |  |  |
| atus-2025:0102 |  |  |
| atus-2025:0103 |  |  |
| atus-2025:0104 |  |  |
| atus-2025:0105 |  |  |
| atus-2025:0201 | [scenario:relative-time-reminder](../evals/scenarios/reminders.json) |  |
| atus-2025:0202 | [scenario:allergy-then-meal-plan](../evals/scenarios/memory.json) |  |
| atus-2025:0203 |  |  |
| atus-2025:0204 |  |  |
| atus-2025:0205 |  | [judgment:followup-topic-en](../evals/judgments/cases-v1.json), [judgment:followup-topic-ko](../evals/judgments/cases-v1.json), [judgment:steer-unrelated-en](../evals/judgments/cases-v1.json), [judgment:steer-unrelated-ko](../evals/judgments/cases-v1.json) |
| atus-2025:0206 |  |  |
| atus-2025:0207 |  |  |
| atus-2025:0208 |  |  |
| atus-2025:0209 | [scenario:allergy-then-meal-plan](../evals/scenarios/memory.json), [scenario:preference-then-recommendation](../evals/scenarios/memory.json), [scenario:recall-correct-printer](../evals/scenarios/recall-experiments.json), [scenario:recall-correct-size](../evals/scenarios/recall-experiments.json), [scenario:recall-parcel-again](../evals/scenarios/recall-experiments.json), [scenario:workplace-statement-remember](../evals/scenarios/memory.json), [scenario:world-appworld-note-update](../evals/scenarios/world-appworld.json) | [judgment:followup-correction-en](../evals/judgments/cases-v1.json), [judgment:followup-correction-ko](../evals/judgments/cases-v1.json), [judgment:steer-correction-en](../evals/judgments/cases-v1.json), [judgment:steer-correction-ko](../evals/judgments/cases-v1.json) |
| atus-2025:0301 |  |  |
| atus-2025:0302 |  |  |
| atus-2025:0303 |  |  |
| atus-2025:0304 |  |  |
| atus-2025:0305 |  |  |
| atus-2025:0401 |  |  |
| atus-2025:0402 |  |  |
| atus-2025:0403 |  |  |
| atus-2025:0404 |  |  |
| atus-2025:0405 |  |  |
| atus-2025:0501 | [scenario:recall-dated-studio](../evals/scenarios/recall-experiments.json), [scenario:recall-quiet-desk](../evals/scenarios/recall-experiments.json) |  |
| atus-2025:0502 | [scenario:recall-hall-again](../evals/scenarios/recall-experiments.json) |  |
| atus-2025:0503 |  |  |
| atus-2025:0504 |  |  |
| atus-2025:0601 |  |  |
| atus-2025:0602 |  |  |
| atus-2025:0603 |  |  |
| atus-2025:0604 |  |  |
| atus-2025:0701 | [scenario:skill-add-reference-by-name](../evals/scenarios/skills.json) |  |
| atus-2025:0702 | [scenario:line-change-stock-time](../evals/scenarios/continuity-experiments.json), [scenario:new-request-after-lunch-topic](../evals/scenarios/topic-shift.json), [scenario:new-request-after-travel-topic](../evals/scenarios/topic-shift.json), [scenario:shopping-followup-constraint](../evals/scenarios/shopping.json), [scenario:shopping-lookup-compare](../evals/scenarios/shopping.json), [scenario:standing-wish-is-not-a-memory](../evals/scenarios/watches.json) | [judgment:escalation-en](../evals/judgments/cases-v1.json), [judgment:escalation-ko](../evals/judgments/cases-v1.json), [judgment:goal-partial](../evals/judgments/cases-v1.json), [judgment:goal-partial-ko](../evals/judgments/cases-v1.json), [judgment:open-question-true-en](../evals/judgments/cases-v1.json), [judgment:open-question-true-ko](../evals/judgments/cases-v1.json) |
| atus-2025:0703 |  |  |
| atus-2025:0801 |  |  |
| atus-2025:0802 | [scenario:world-appworld-pay-approved](../evals/scenarios/world-appworld.json), [scenario:world-appworld-pay-denied](../evals/scenarios/world-appworld.json) | [judgment:effect-payment](../evals/judgments/cases-v1.json) |
| atus-2025:0803 |  |  |
| atus-2025:0804 |  |  |
| atus-2025:0805 |  |  |
| atus-2025:0806 |  |  |
| atus-2025:0807 |  |  |
| atus-2025:0808 |  |  |
| atus-2025:0901 |  |  |
| atus-2025:0902 |  |  |
| atus-2025:0903 |  |  |
| atus-2025:0904 |  |  |
| atus-2025:0905 |  |  |
| atus-2025:1001 |  |  |
| atus-2025:1002 |  |  |
| atus-2025:1003 |  |  |
| atus-2025:1004 |  |  |
| atus-2025:1101 | [scenario:already-ate-dinner-next-meal](../evals/scenarios/food.json), [scenario:already-ate-lunch](../evals/scenarios/food.json), [scenario:food-on-route-budget-correction](../evals/scenarios/food.json), [scenario:food-on-route-rest-area](../evals/scenarios/food.json), [scenario:new-request-after-lunch-topic](../evals/scenarios/topic-shift.json), [scenario:preference-then-recommendation](../evals/scenarios/memory.json), [scenario:standing-wish-becomes-watch](../evals/scenarios/watches.json), [scenario:weekday-lunch-near-workplace](../evals/scenarios/food.json) |  |
| atus-2025:1102 |  |  |
| atus-2025:1201 | [scenario:presence-chat-short-reply-stays-short](../evals/scenarios/presence.json), [scenario:recall-dated-committee](../evals/scenarios/recall-experiments.json), [scenario:world-appworld-send-text](../evals/scenarios/world-appworld.json) | [judgment:goal-unknown-ko](../evals/judgments/cases-v1.json), [judgment:owner-act-approve](../evals/judgments/cases-v1.json), [judgment:owner-act-decline](../evals/judgments/cases-v1.json), [judgment:owner-act-none](../evals/judgments/cases-v1.json) |
| atus-2025:1202 |  |  |
| atus-2025:1203 | [scenario:line-cancel-watch](../evals/scenarios/continuity-experiments.json), [scenario:line-change-watch-time](../evals/scenarios/continuity-experiments.json), [scenario:line-evening-watch](../evals/scenarios/continuity-experiments.json), [scenario:line-past-submission-deadline](../evals/scenarios/continuity-experiments.json), [scenario:line-status-before-first-run](../evals/scenarios/continuity-experiments.json), [scenario:recall-clay-language](../evals/scenarios/recall-experiments.json), [scenario:recall-expired-pass](../evals/scenarios/recall-experiments.json), [scenario:recall-forget-project](../evals/scenarios/recall-experiments.json), [scenario:recall-irrelevant-music](../evals/scenarios/recall-experiments.json) |  |
| atus-2025:1204 | [scenario:line-add-watch-rule](../evals/scenarios/continuity-experiments.json), [scenario:line-cancel-seat-watch](../evals/scenarios/continuity-experiments.json) |  |
| atus-2025:1205 |  |  |
| atus-2025:1301 | [scenario:correct-earlier-advice-hiking](../evals/scenarios/corrections.json), [scenario:recall-forget-route](../evals/scenarios/recall-experiments.json), [scenario:tee-time-reminder](../evals/scenarios/reminders.json) | [judgment:plan-schedule](../evals/judgments/cases-v1.json) |
| atus-2025:1302 |  |  |
| atus-2025:1303 |  |  |
| atus-2025:1304 |  |  |
| atus-2025:1401 |  |  |
| atus-2025:1501 |  |  |
| atus-2025:1502 |  |  |
| atus-2025:1503 |  |  |
| atus-2025:1504 |  |  |
| atus-2025:1505 |  |  |
| atus-2025:1506 |  |  |
| atus-2025:1507 |  |  |
| atus-2025:1508 |  |  |
| atus-2025:1601 |  |  |
| atus-2025:1602 |  |  |
| atus-2025:1801 |  |  |
| atus-2025:1802 |  |  |
| atus-2025:1803 |  |  |
| atus-2025:1804 |  |  |
| atus-2025:1805 | [scenario:presence-chat-lookup-with-a-judgment](../evals/scenarios/presence.json), [scenario:settings-change-with-confirmation](../evals/scenarios/settings.json), [scenario:travel-time-after-event](../evals/scenarios/travel.json), [scenario:travel-time-with-clock-note](../evals/scenarios/travel.json) |  |
| atus-2025:1806 |  |  |
| atus-2025:1807 |  |  |
| atus-2025:1808 |  |  |
| atus-2025:1809 |  |  |
| atus-2025:1810 |  |  |
| atus-2025:1811 | [scenario:food-on-route-budget-correction](../evals/scenarios/food.json), [scenario:food-on-route-rest-area](../evals/scenarios/food.json) |  |
| atus-2025:1812 | [scenario:new-request-after-travel-topic](../evals/scenarios/topic-shift.json), [scenario:presence-chat-research-takes-a-while](../evals/scenarios/presence.json), [scenario:travel-time-from-earlier-place](../evals/scenarios/travel.json) |  |
| atus-2025:1813 | [scenario:tee-time-departure-reminder](../evals/scenarios/reminders.json) |  |
| atus-2025:1814 |  |  |
| atus-2025:1815 |  |  |
| atus-2025:1816 |  |  |
| atus-2025:1818 |  |  |
| onet-31.0-gwa:4.A.1.a.1 |  |  |
| onet-31.0-gwa:4.A.1.a.2 |  |  |
| onet-31.0-gwa:4.A.1.b.1 |  |  |
| onet-31.0-gwa:4.A.1.b.2 |  |  |
| onet-31.0-gwa:4.A.1.b.3 |  |  |
| onet-31.0-gwa:4.A.2.a.1 |  |  |
| onet-31.0-gwa:4.A.2.a.2 |  |  |
| onet-31.0-gwa:4.A.2.a.3 |  |  |
| onet-31.0-gwa:4.A.2.a.4 |  |  |
| onet-31.0-gwa:4.A.2.b.1 |  |  |
| onet-31.0-gwa:4.A.2.b.2 |  |  |
| onet-31.0-gwa:4.A.2.b.3 |  |  |
| onet-31.0-gwa:4.A.2.b.4 |  |  |
| onet-31.0-gwa:4.A.2.b.5 |  |  |
| onet-31.0-gwa:4.A.2.b.6 |  |  |
| onet-31.0-gwa:4.A.3.a.1 |  |  |
| onet-31.0-gwa:4.A.3.a.2 |  |  |
| onet-31.0-gwa:4.A.3.a.3 |  |  |
| onet-31.0-gwa:4.A.3.a.4 |  |  |
| onet-31.0-gwa:4.A.3.b.1 | [scenario:settings-change-with-confirmation](../evals/scenarios/settings.json), [scenario:settings-read-in-conversation](../evals/scenarios/settings.json), [scenario:skill-add-reference-by-name](../evals/scenarios/skills.json) | [judgment:plan-password](../evals/judgments/cases-v1.json), [judgment:plan-technical](../evals/judgments/cases-v1.json) |
| onet-31.0-gwa:4.A.3.b.2 |  |  |
| onet-31.0-gwa:4.A.3.b.4 |  |  |
| onet-31.0-gwa:4.A.3.b.5 |  |  |
| onet-31.0-gwa:4.A.3.b.6 | [scenario:recall-binding-language](../evals/scenarios/recall-experiments.json), [scenario:recall-reading-format](../evals/scenarios/recall-experiments.json) | [judgment:goal-failed](../evals/judgments/cases-v1.json), [judgment:goal-failed-ko](../evals/judgments/cases-v1.json), [judgment:goal-unknown](../evals/judgments/cases-v1.json) |
| onet-31.0-gwa:4.A.4.a.1 |  | [judgment:goal-served](../evals/judgments/cases-v1.json), [judgment:goal-served-ko](../evals/judgments/cases-v1.json), [judgment:plan-plain-en](../evals/judgments/cases-v1.json), [judgment:plan-plain-ko](../evals/judgments/cases-v1.json) |
| onet-31.0-gwa:4.A.4.a.2 |  |  |
| onet-31.0-gwa:4.A.4.a.3 |  |  |
| onet-31.0-gwa:4.A.4.a.4 |  |  |
| onet-31.0-gwa:4.A.4.a.5 |  |  |
| onet-31.0-gwa:4.A.4.a.6 |  |  |
| onet-31.0-gwa:4.A.4.a.7 |  |  |
| onet-31.0-gwa:4.A.4.a.8 |  |  |
| onet-31.0-gwa:4.A.4.b.1 |  |  |
| onet-31.0-gwa:4.A.4.b.2 |  |  |
| onet-31.0-gwa:4.A.4.b.3 |  |  |
| onet-31.0-gwa:4.A.4.b.4 |  |  |
| onet-31.0-gwa:4.A.4.b.5 |  |  |
| onet-31.0-gwa:4.A.4.b.6 |  |  |
| onet-31.0-gwa:4.A.4.c.1 |  |  |
| onet-31.0-gwa:4.A.4.c.2 |  |  |
| onet-31.0-gwa:4.A.4.c.3 |  |  |
