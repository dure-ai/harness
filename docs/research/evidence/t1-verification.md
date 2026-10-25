# T1 출처 검증 보고서

- 대상: `.superpowers/research/summary-source.md` (AI 생성 리서치 요약, 한국어)
- 검증일: 2026-10-25
- 방법: arXiv abs/html, 저자·벤더 페이지, GitHub, `curl -I`(HTTP 상태), 웹 검색(extended 포함). 페이지 본문은 WebFetch(요약 모델 경유)로 읽었기 때문에, 표의 세부 수치는 "해당 페이지 본문 기준"이며 원문 PDF를 직접 대조하지는 못했다(PDF는 텍스트 추출 도구가 없어 열람 불가).
- 판정 요약: 확인됨 28 / 일부 불일치 7 / 확인 불가 5 / 존재하지 않음 0 (총 40행). 요약이 인용한 출처 중 가짜(실재하지 않는)로 판명된 것은 없었다. 문제는 존재 여부가 아니라 "출처가 실제로 말하는 것보다 과장·단순화·귀속 오류"인 부분이다.

## 1. 검증 표

| # | 요약의 주장/인용 | 링크 | 판정 | 근거(실제 제목·저자·날짜·핵심 수치와 출처) | 비고 |
|---|---|---|---|---|---|
| 1 | Mem0: 범용 메모리 레이어(GitHub, 문서) | github.com/mem0ai/mem0, docs.mem0.ai | 확인됨 | 두 URL 모두 HTTP 200. Mem0 논문은 arXiv:2504.19413 (Chhikara 외, 2025-04-28). | 요약은 논문 자체는 인용하지 않음. |
| 2 | Cognee: 그래프 네이티브 메모리, GitHub/웹사이트 | github.com/topoteretes/cognee, cognee.ai | 확인됨 | 둘 다 HTTP 200. | 벤더 사이트. |
| 3 | "Cognee는 가장 활발히 채택되고 있는" 메모리 컨트롤 플레인 | (출처 없음) | 확인 불가 | 채택 순위를 뒷받침하는 독립 자료를 찾지 못함. | 마케팅성 문장. 본문 사용 시 삭제 또는 근거 필요. |
| 4 | Letta (구 MemGPT): OS 페이징형 상태 유지 에이전트 | github.com/letta-ai/letta, docs.letta.com | 확인됨 | 둘 다 200. MemGPT 논문의 "virtual context management"와 일치(행 6). | |
| 5 | Zep: 시간축 지식 그래프, "이중 시간(Bi-temporal)" | github.com/getzep/zep, help.getzep.com | 확인됨 | 두 URL 200. "Zep: A Temporal Knowledge Graph Architecture for Agent Memory", Rasmussen 외, arXiv:2501.13956, 2025-01-20. | 초록은 "temporally-aware"까지만 말하고 "bi-temporal" 표현은 초록에 없음(본문 미확인). 요약은 이 표현을 "상용 엔진들" 일반으로 확대. |
| 6 | MemGPT: Towards LLMs as Operating Systems | arxiv.org/abs/2310.08560 | 확인됨 | Packer 외, 2023-10-12(v2 2024-02-12). 제목·내용 일치. | |
| 7 | Generative Agents: Interactive Simulacra of Human Behavior | arxiv.org/abs/2304.03442 | 확인됨 | Park 외, 2023-04-07. 관찰·반추·계획 구조 일치. | |
| 8 | Anthropic: Building Effective Agents (과도한 프레임워크 경계) | anthropic.com/research/building-effective-agents | 확인됨 | 2024-12-19. 프레임워크가 추상화 계층을 더해 디버깅을 어렵게 하며 LLM API 직접 사용으로 시작하라고 권고. | "하네스를 경계"는 약간의 의역이나 취지는 맞음. |
| 9 | Anthropic: Contextual Retrieval | anthropic.com/news/contextual-retrieval | 확인됨 | HTTP 200, `/research/contextual-retrieval`로 리다이렉트. | URL만 확인, 본문 수치는 열람하지 않음. 요약은 수치를 인용하지 않으므로 영향 없음. |
| 10 | LangGraph Memory Concepts (단기/장기 체크포인팅) | langchain-ai.github.io/langgraph/concepts/memory/ | 확인 불가 | 200이지만 "Redirecting..." 스텁만 반환, 최종 문서 내용을 열람하지 못함. | 링크가 이전된 것으로 보임. 사용 전 최종 URL 재확인 필요. |
| 11 | LongMemEval (정보 갱신·시간 추론·다중 세션 통합) | arxiv.org/abs/2410.10813, github.com/xiaowu0162/LongMemEval | 확인됨 | "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory", Wu 외, 2024-10-14, ICLR 2025. 500문항, 5개 능력(정보 추출·다중 세션 추론·시간 추론·지식 갱신·기권), 상용 어시스턴트·장문맥 LLM이 약 30% 정확도 하락. repo 200. | 요약 설명과 일치. |
| 12 | LoCoMo (초장기 대화 기억 벤치마크) | arxiv.org/abs/2402.17753, github.com/snap-research/locomo | 확인됨 | "Evaluating Very Long-Term Conversational Memory of LLM Agents", Maharana 외, 2024-02-27. repo 200. | 요약은 "LoCoMo (Long-Context Modeling)"로 풀었으나 약어 해설은 출처에 없음(사소). |
| 13 | 평가 하네스 repo: lm-evaluation-harness, inspect_ai, SWE-bench, Ragas | github.com/EleutherAI/..., UKGovernmentBEIS/inspect_ai, SWE-bench/SWE-bench, explodinggradients/ragas | 확인됨 | 모두 200. ragas는 `vibrantlabsai/ragas`로 리다이렉트(소유 org 이전). | ragas URL은 갱신 권장. |
| 14 | "LoCoMo는 약 2.6만 토큰이라 최신 모델 컨텍스트 윈도우에 통째로 들어간다" | (인용 없음; 근거 후보 Mem0 논문, cognee 가이드) | 일부 불일치 | 26k는 LoCoMo 원 논문이 아니라 Mem0 논문(arXiv:2504.19413)의 수치(Table 2 "26031", "roughly 26,000"). LoCoMo 원 논문(2402.17753)은 평균 "9K tokens"(최대 35 세션)라고 기술. Cognee 가이드(2026-09-01)는 "공개본의 가장 긴 10개 대화가 평균 약 26k"라고 설명. | 결론("현대 컨텍스트 윈도우에 들어감")은 맞지만 수치의 출처·정의가 모호. 쓸 때는 "대화당 약 9k~26k 토큰(집계 방식에 따라 다름)"으로 표기. |
| 15 | Memory for Autonomous LLM Agents (arXiv:2603.07670) | arxiv.org/abs/2603.07670 | 확인됨 | "Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers", Pengfei Du, 2026-03-08 (cs.AI). 서베이. | 요약은 이를 "분석 연구"처럼 서술했으나 실제로는 서베이(2차 문헌). 아래 행 16 참조. |
| 16 | "LoCoMo 만점에 가까운 시스템이 실제 에이전트 환경에서 40~60%대로 폭락 (arXiv:2603.07670)" | arxiv.org/abs/2603.07670 | 일부 불일치 | 해당 문장은 서베이에 실재함: §3.4 Table 1 "near-saturated LoCoMo models drop to 40–60%", §5.2에서도 반복(HTML 본문 기준). 그러나 (a) 서베이는 어떤 모델·점수에서 나온 수치인지 제시하지 않고 1차 출처로 He et al. 2026(MemoryArena)을 인용할 뿐이고, (b) 1차 논문 MemoryArena(arXiv:2602.16313)는 초록에서 "perform poorly"라고만 쓰고 40~60% 수치를 싣지 않음. 1차 논문 Table 3의 전체 평균 task success rate는 Letta 0.14, Mem0 0.13, Mem0-g 0.12, 장문맥 0.09~0.19, RAG 최대 0.20으로 40~60%와 맞지 않음(진행 점수 PS는 더 높으나 별도 지표). | 이 수치는 "서베이 한 줄 주장"일 뿐 1차 근거로 역추적되지 않음. 인용하려면 "서베이가 보고한" 형태로만 쓰고, 수치 자체를 핵심 근거로 삼지 말 것. 반대로 MemoryArena 자체 결과(메모리 에이전트가 장문맥·RAG보다 뚜렷이 낫지 않음)는 이 프로젝트에 더 유용한 사실. |
| 17 | MemoryArena (기억·행동 결합) | (요약은 링크 없이 이름만) | 확인됨 | "MemoryArena: Benchmarking Agent Memory in Interdependent Multi-Session Agentic Tasks", Zexue He 외 14명, arXiv:2602.16313, v1 2026-02-18, v2 2026-09-17, 코멘트 "ICML 2026". 웹 쇼핑·그룹 여행 계획·점진적 정보 검색·순차 형식 추론. | ICML 2026은 arXiv 코멘트·ICML 포스터 페이지로 확인. |
| 18 | DolphinBench (기억·행동 결합) | (요약은 링크 없이 이름만) | 확인됨 | "DolphinBench: Mapping the Pareto Frontier of Agent Memory", Soumil Rathi, Deshraj Yadav, Taranjeet Singh, arXiv:2609.24971, 2026-09-21. 3개 페르소나, 페르소나당 약 500k 토큰 히스토리·200개 검증 과제, 정확도·비용·지연 동시 보고. | 요약이 밝히지 않은 점: Mem0 자사 벤치마크(저자 Yadav·Singh는 Mem0 논문 저자, repo는 github.com/mem0ai/dolphinbench, Mem0 블로그 발표). 최상위 구성(70.67%)도 Mem0 포함. 이해충돌 주의. arXiv 페이지 자체에는 소속 표기 없음. |
| 19 | STATE-Bench (절차적 경험 축적 평가) | (요약은 링크 없이 이름만) | 확인됨 | Microsoft Open Source 블로그 "Introducing STATE-Bench", 2026-05-19, 저자 Lewis Liu·Nishant Yadav. 450과제(고객지원·여행·쇼핑), 메모리 비종속, repo github.com/microsoft/STATE-Bench. 메모리 없는 GPT-5.1 기준 여행 과제 pass^5 약 30%. | 논문이 아니라 블로그+repo. 같은 팀의 Foundry 블로그는 절차 기억으로 Pass¹ 53.3→58.3%(+5.0) 보고(벤더 자체 결과). |
| 20 | BEAM (최대 10M 토큰 규모) | (요약은 링크 없이 이름만) | 확인됨 | "Beyond a Million Tokens: Benchmarking and Enhancing Long-Term Memory in LLMs", Tavakoli 외, arXiv:2510.27246, 2025-10-31, ICLR 2026. 100개 대화·2,000문항, 최대 10M 토큰, LIGHT 아키텍처(평균 +3.5~12.69%). | |
| 21 | MemoryAgentBench (초장기 스케일 테스트, 100만~1,000만 토큰) | (요약은 링크 없이 이름만) | 확인됨 | "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions", Hu·Wang·McAuley, arXiv:2507.05257, 2025-07-07(v4 2026-06-28). 4개 능력(정확 검색·테스트 시점 학습·장거리 이해·선택적 망각). ICLR 2026 포스터 페이지 존재(검색 결과). | 벤치마크 존재·핵심 설명은 일치. 다만 "100만~1,000만 토큰" 규모는 BEAM에서만 확인되었고 MemoryAgentBench의 토큰 규모는 확인하지 못함(arXiv 초록 미기재). arXiv 페이지 코멘트에는 학회 표기 없음. |
| 22 | "상용 엔진은 토큰 비용을 최대 90%까지 절감" | (인용 없음; 근거 후보 Mem0 논문) | 일부 불일치 | 출처는 Mem0 논문 초록 "saves more than 90% token cost"(vs 전체 컨텍스트 투입). 본문 기준 같은 논문에서 정확도(J)는 full-context 72.90 > Mem0 66.88로 full-context가 더 높음. Mem0의 2026 블로그(state-of-ai-agent-memory-2026)에는 90% 수치가 없고 6,956 대 약 26,000 토큰(약 73% 감소)이 나오되 단위가 달라 근사치라고 명시. | 벤더 자체 측정이고 기준선이 "전체 대화 투입"이라 약한 비교. "상용 엔진 일반의 최대 90% 절감"으로 일반화하면 과장. 추출 단계 비용 포함 여부도 불명확. |
| 23 | Online Agent-as-a-Judge (arXiv:2606.08200) | arxiv.org/abs/2606.08200 | 확인됨 | "Online Agent-as-a-Judge: Situation-Generating Evaluation for Interactive Agents", Hyogon Ryu 외 5명, 2026-06-06. 평가자 에이전트가 환경 안에서 상황을 직접 생성해 기준을 검증. | 제목·ID 일치. |
| 24 | "오프라인 심판 대비 실패 탐지율이 수 배 이상 높다" | 위와 동일 | 일부 불일치 | 논문은 "실패 탐지율"이 아니라 (a) 기준 커버리지 0.92 vs 0.54~0.56, (b) 인간 라벨 일치도 0.70 vs 0.33~0.40, (c) fail 라벨 정확도 0.60(144건 중 87) vs 오프라인 LLM 심판 0.08, 오프라인 에이전트 심판 0.32를 보고(HTML 본문 기준). 환경은 5인 가족 생활 시뮬레이션이고 32개 설계자 작성 사회적 기준. 논문 문구는 "substantially higher"이며 "x배" 표현은 없음. | "수 배"는 LLM 심판 대비 약 7.5배에서만 성립하고 오프라인 에이전트 심판 대비는 약 1.9배. 또한 사회적 에이전트/생활 시뮬레이션 맥락이며 일반 에이전트의 "엣지 케이스" 일반 결과가 아님. 수치는 모델 경유 추출이라 PDF 대조 권장. |
| 25 | Arize: Agent-as-a-Judge 실무 가이드, "상용 도입" | arize.com/guides/agent-as-a-judge/ | 확인됨 | 페이지 존재, 제목 "Agent-as-a-Judge: A practical guide to agentic evaluation", 최종 검토 2026-10-07, "Arize brought agentic judging into Arize AX in 2026". | 요약이 말한 "SQL 쿼리·파일 diff로 증거 수집"은 해당 페이지에서 확인되지 않음(기록된 tool call·결과를 조사한다고만 설명). 원형 논문은 Zhuge 외 arXiv:2410.10934(요약에 누락). |
| 26 | Anthropic: Demystifying evals for AI agents (결과 중심 채점·환경 격리) | anthropic.com/research/demystifying-evals-for-ai-agents | 확인됨 | 2026-01-09 발행. "과정이 아니라 산출물을 채점", 고정된 도구 호출 순서 검사는 지나치게 경직, 시행마다 깨끗한 환경, LLM 채점기는 인간 전문가로 보정 권고. 코드·모델·인간 채점기 조합. | "하이브리드 채점이 표준화되고 있다"는 표현은 과장(글은 권고 수준). 요약의 "State-diff 코드 검사 + 루브릭 심판" 구도는 취지에 부합. |
| 27 | Meta-Harness: End-to-End Optimization of Model Harnesses | yoonholee.com/meta-harness/ | 확인됨 | Yoonho Lee, Roshen Nair, Qizheng Zhang, Kangwook Lee, Omar Khattab, Chelsea Finn. arXiv:2603.28052, 2026-03-30. 에이전트 제안자가 이전 후보의 소스·점수·실행 트레이스를 파일시스템으로 읽고 하네스 코드를 탐색. 텍스트 분류 +7.7점(컨텍스트 토큰 4배 감소), IMO급 수학 검색 +4.7점, TerminalBench-2 상위. | 요약의 기술은 맞음. 다만 대상은 "메모리 저장/검색 로직"에 한정되지 않고 프롬프트·도구·검색·컨텍스트 관리 등 하네스 전반. |
| 28 | "Meta-Harness (ICML 2026 발표)" | 위와 동일 | 일부 불일치 | 저자 페이지 BibTeX는 `Conference on Language Modeling (COLM)` 2026으로 표기. arXiv·GitHub 페이지에는 학회 표기 없음. ICML 2026에는 제목이 다른 "Meta-Harness: Post-Training Reliable Agent Systems via Harness Search"가 워크숍(Agents in the Wild) 포스터로 올라와 있음(icml.cc/virtual/2026/67972). 두 항목이 같은 논문이라는 확인은 페이지에 없음. | "ICML 2026"은 본 학회 채택으로 보면 틀리고, 저자 자체 표기는 COLM 2026. 쓸 때는 "arXiv 2026-03 프리프린트(저자 표기: COLM 2026)"로. |
| 29 | Agentic Harness Engineering (AHE): 프롬프트·도구 미들웨어·장기 메모리 스키마를 반복 루프로 진화 | arxiv.org/abs/2604.25850 | 확인됨 | "Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses", Jiahang Lin 외, 2026-04-28(v4 2026-05-18). Terminal-Bench 2 69.7→77.0%(10회 반복), SWE-bench-verified에서 시드 대비 12% 적은 토큰. 학회 표기 없음. | 초록의 ablation은 이득의 원천을 "tools, middleware, long-term memory"로 보고 시스템 프롬프트는 원천이 아니라고 함 → 요약의 "프롬프트 진화" 서술은 부정확. 코딩 에이전트 하네스 한정이며 메모리 엔진 평가 맥락과는 거리. |
| 30 | AEVO: AHE와 같은 계열의 관측 기반 하네스 자동 진화 | (요약은 링크 없이 이름만) | 일부 불일치 | 실제 논문은 "Harnessing Agentic Evolution", Jiayi Zhang 외 13명, arXiv:2605.13821, 2026-05-13. 이름은 "AEvo". 메타 에이전트가 후보를 직접 제안하는 대신 진화 절차/컨텍스트를 편집. 5개 진화 기준선 대비 상대 +26%. 학회 표기 없음. | AHE와 "프롬프트·도구·메모리 스키마를 진화"로 묶은 서술은 AEvo에 맞지 않음(진화 절차 자체를 메타 편집하는 프레임워크). 표기는 AEvo. |
| 31 | Rethinking the Evaluation of Harness Evolution for Agents (arXiv:2607.12227) | arxiv.org/abs/2607.12227 | 확인됨 | Yike Wang 외 11명(Hajishirzi, Tsvetkov, Noah A. Smith, Dasigi 포함), v1 2026-07-14, v4 2026-10-05. | 제목·ID 일치. |
| 32 | "COLM 2026" 발표 | 위와 동일 | 확인 불가 | arXiv 초록 페이지, HTML 본문, HuggingFace 페이지, 저자 GitHub org(rethinking-harness-evolution) 어디에도 학회 표기 없음. "accepted" 표시도 없음. | 요약의 COLM 2026은 근거를 찾지 못함. 프리프린트로 표기. (COLM 2026 채택 목록 자체는 열람하지 못함) |
| 33 | 해당 논문이 "하네스 과적합 반박, 단순 모델+test-time scaling이 held-out에서 더 잘 일반화, '하네스 인지 평가' 강조" | 위와 동일 | 일부 불일치 | 본문 기준: Terminal-Bench 2.1에서 하네스 진화는 단순 test-time scaling을 이기지 못함(무 테스트: Parallel Sampling 72.3 vs Harness Evolution 67.4; 유 테스트: 82.0 vs 69.7). 학습/검증/테스트 45/10/34 분할에서 테스트 평균 +0.0(63.2→63.2)이고 저자들은 "severe overfitting to the training tasks"라고 표현. 한편 "harness-aware evaluation"이라는 용어는 이 논문에 없음. 이 논문은 장기 게임에서는 하네스 진화가 유효하다고 보고(ARC-AGI-3 +80%, EdgeBench +11%). | (a) 과적합 주장은 지지됨, (b) "Harness-Aware Evaluation" 귀속은 오류(별도 논문들의 용어, 행 34), (c) "진화가 아니라 과적합일 뿐"은 단정이 과하고 긍정 결과(장기 게임)를 누락. 이 논문의 결론은 "매칭된 예산의 기준선과 held-out 평가를 요구". |
| 34 | Harness-Aware Evaluation of LLM Agents | preprints.org/manuscript/202609.2140 | 확인됨 | 직접 접속은 HTTP 403이라 열람 못함. 검색 결과 및 저자 repo(VectorInstitute/harness-aware-evaluation)로 확인: 전체 제목 "Harness-Aware Evaluation of LLM Agents: Systems, Benchmarks, and Protocols", Radwan·Vasilakos·Raza, 2026-09-24, DOI 10.20944/preprints202609.2140.v1, Preprints.org 상 "NOT PEER-REVIEWED" 서베이. | 요약은 제목이 잘려 있고, 이 서베이가 "Rethinking" 논문의 연장선이라는 근거도 없음(Rethinking은 인용 목록에 이름만 있음). 동일 개념의 더 먼저 나온 입장 논문: arXiv:2605.23950(섹션 3 누락 자료 4번). |
| 35 | Cognee AI Memory Benchmarks 가이드(LoCoMo 한계·MemoryArena·BEAM·STATE-Bench 비교) | cognee.ai/ai-memory-benchmarks | 확인됨 | 페이지 존재, "AI Memory Benchmarks: The Complete Guide (2026)", 2026-09-01(수정 09-03). BEAM 4개 길이 구간·MemoryArena 766과제·STATE-Bench 450과제 등을 설명. | 벤더(Cognee) 블로그. 자사 점수(BEAM 0.79/0.67) 포함. |
| 36 | Mem0 State of AI Agent Memory 2026 | mem0.ai/blog/state-of-ai-agent-memory-2026 | 확인됨 | 페이지 존재, "State of AI Agent Memory 2026: Benchmarks & Trends"(최초 2026-04-01, 갱신 2026-10-10). BEAM 1M 64.1 / 10M 48.6. | 벤더 블로그. 이 글은 MemoryArena를 언급하지 않고 90% 수치도 없음(행 22). 요약이 "LoCoMo 한계와 MemoryArena·BEAM·STATE-Bench 비교"라는 설명을 이 링크에 단 부분은 cognee 페이지에 해당. |
| 37 | "4W Memory Taxonomy" | (출처 없음) | 확인 불가 | "4W"로 불리는 기억 분류 체계를 어떤 검색에서도 찾지 못함. 근접 후보: 형태·기능·동역학 분류(arXiv:2512.13564), 시간 범위·표현 기반·제어 정책 3차원 분류(2603.07670), CoALA의 작업·의미·일화·절차 4분류. | "존재하지 않음"으로 단정할 증거는 없으나 근거 없는 용어. 사용하지 말 것. |
| 38 | Evermind EverOS: 멀티모달, 궤적을 Case로 저장하고 Skill로 추출 | (요약은 링크 없이 이름만) | 확인됨 | GitHub `EverMind-AI/EverOS`, 마크다운 기반 로컬 우선 메모리 런타임. Cases/Skills, 멀티모달 수집(PDF·이미지 등). | 대부분 벤더 자료·보도자료·제3자 소개(MarkTechPost 포함). 벤더 보고 수치(LoCoMo 93.05% 등)는 검증하지 않음. |
| 39 | "타임아웃·컨테이너 리소스만 늘려도 에이전트 벤치마크 점수가 크게 뛴다" | (인용 없음) | 확인됨 | Anthropic "Quantifying infrastructure noise in agentic coding evals"(2026-02경): Terminal-Bench 2.0에서 가장 여유로운 설정과 가장 빡빡한 설정 간 약 6%p 차. 인프라 오류율 약 5.8%→2.1%(3배 자원). | 1차 글은 열람하지 못하고 다수 2차 요약(GIGAZINE, ZenML 등)에 의존. 자원 3배까지의 성공률 변화는 노이즈 범위 안이라는 세부도 있음. |
| 40 | LLM 심판의 장황함 편향·자기 선호 편향·비결정성, 정규식 파서 민감도 | (인용 없음) | 확인 불가 | 이 항목은 검증 시도 범위에서 제외(출처가 제시되지 않았고 개별 논문 검색은 하지 않음). | 일반론으로 널리 알려져 있으나 이 보고서에서는 검증하지 않았음을 명시. 본문에 쓸 경우 별도 근거 필요. |

## 2. 신뢰해도 되는 것 / 쓰면 안 되는 것

### 신뢰해도 되는 것 (1차 출처로 확인)
- 논문·벤치마크의 존재와 서지: MemGPT, Generative Agents, LongMemEval(ICLR 2025), LoCoMo, MemoryArena(arXiv:2602.16313, ICML 2026), BEAM(ICLR 2026), MemoryAgentBench, Online Agent-as-a-Judge(2606.08200), Meta-Harness(2603.28052), AHE(2604.25850), AEvo(2605.13821), Rethinking(2607.12227).
- 벤치마크 설계 설명: LongMemEval의 5개 능력, BEAM의 최대 10M 토큰, STATE-Bench의 450과제·절차적 경험 초점, MemoryArena의 다중 세션 상호의존 과제.
- 방향성: 메모리 평가가 "수동 회상 QA"에서 "메모리가 행동에 반영되는지"로 이동 중이라는 큰 그림, 하네스 진화의 평가가 매칭 예산 기준선·held-out 평가를 필요로 한다는 점, 결과 상태 기반 채점·환경 격리를 권하는 Anthropic 글, 인프라 구성이 에이전트 벤치마크 점수를 수 %p 움직인다는 점.

### 쓰면 안 되는 것 (또는 수정해서 써야 하는 것)
1. "LoCoMo 고득점 시스템이 에이전트 작업에서 40~60%로 하락(2603.07670)": 서베이 한 줄 주장이고 1차 논문(MemoryArena)에서 역추적되지 않음. 인용 시 "서베이가 주장"이라고 한정하고 핵심 근거로 쓰지 말 것. 대신 MemoryArena 자체 결과(전체 평균 success rate 최대 약 0.20, 메모리 에이전트가 장문맥·RAG를 뚜렷이 앞서지 못함)를 쓰는 편이 정확.
2. "토큰 비용 최대 90% 절감": Mem0 논문 한 건(벤더, 전체 컨텍스트 대비)이며 같은 논문에서 정확도는 full-context가 더 높음. 일반화 금지.
3. "Online Agent-as-a-Judge가 실패 탐지율 수 배": 사회적 에이전트 생활 시뮬레이션에서의 fail-label 정확도 비교(0.60 vs 0.08/0.32)이고 "탐지율 x배" 표현이 아님. 조건 명시 없이 일반화 금지.
4. "Meta-Harness = ICML 2026", "Rethinking = COLM 2026": 전자는 저자 표기가 COLM 2026이고 ICML에는 워크숍 포스터만 확인, 후자는 학회 표기 없음. 둘 다 "arXiv 프리프린트"로 표기.
5. "Rethinking이 Harness-Aware Evaluation을 강조": 귀속 오류. 해당 용어는 다른 논문(2605.23950, preprints.org 서베이)의 것.
6. AEVO를 AHE와 동일 계열("프롬프트·도구·메모리 스키마 진화")로 설명한 부분과 "AEVO" 표기: 실제는 AEvo이며 진화 절차 자체를 메타 편집하는 프레임워크.
7. "4W Memory Taxonomy", "Cognee가 가장 활발히 채택": 근거 없음.
8. 벤더 출처 자료를 독립 근거처럼 쓰지 말 것: DolphinBench(Mem0 자체 벤치마크), Cognee/Mem0/Evermind 블로그의 자사 점수와 순위, STATE-Bench 팀의 절차 기억 효과(+5점).
9. LoCoMo "약 2.6만 토큰": 원 논문은 평균 9K(Mem0 논문·Cognee 가이드의 26k는 집계 방식이 다름). 수치를 쓸 때 출처 명시.

### 열람하지 못했거나 한계
- preprints.org 원문은 403으로 직접 열람 불가(검색·저자 repo로만 확인).
- 논문 PDF는 텍스트 추출 도구 부재로 직접 읽지 못했고, 수치는 arXiv HTML을 WebFetch(요약 모델 경유)로 추출한 것이라 소수점 단위는 PDF 대조를 권장(특히 행 16·24·33).
- COLM 2026 채택 목록, MemoryAgentBench의 토큰 규모, LangGraph 이전 후 문서 내용, Anthropic 인프라 노이즈 1차 글은 확인하지 못함.

## 3. 누락된 중요 자료 (2025~2026, 최대 10건)

열람 완료(◎) / 검색 결과 스니펫만 확인(△)

1. ◎ LoCoMo 감사(Penfield Labs, 2026-04): 정답 키 1,540문항 중 99건(6.4%) 오류, 채점기(gpt-4o-mini)가 의도적으로 틀린 주제 근접 답의 62.81%를 통과. 비동료검토이며 단일 감사이고 경쟁 벤더 이해관계가 있음. https://dev.to/penfieldlabs/we-audited-locomo-64-of-the-answer-key-is-wrong-and-the-judge-accepts-up-to-63-of-intentionally-33lg, repo https://github.com/dial481/locomo-audit
2. ◎ Anatomy of Agentic Memory: Taxonomy and Empirical Analysis of Evaluation and System Limitations (arXiv:2602.19320, 2026-02-22): 벤치마크 포화·규모 부족, 지표와 의미적 유용성의 불일치, 백본 모델 의존, 메모리 유지 지연을 지적. https://arxiv.org/abs/2602.19320
3. ◎ Memory in the Age of AI Agents (arXiv:2512.13564, 2025-12-15, 47인 저자 서베이): 형태·기능·동역학 분류와 벤치마크·프레임워크 정리. https://arxiv.org/abs/2512.13564
4. ◎ Stop Comparing LLM Agents Without Disclosing the Harness (arXiv:2605.23950, 2026-05-07): 하네스가 모델보다 성능 분산을 더 크게 만들 수 있고 순위를 뒤집기도 한다는 입장 논문, harness-aware 평가 프레임워크(공개 카드·분산 분해) 제안. https://arxiv.org/abs/2605.23950
5. ◎ Ground Truth First: A Longitudinal Evaluation Instrument for Agent Memory (arXiv:2607.21962, 2026-07-24): 정답이 먼저 정해지는 시계열 메모리 벤치마크, 메모리 아키텍처 순위가 이력 길이(3주 vs 9주)에 따라 뒤집힘, 전체 이력 baseline이 3주에는 최고 메모리와 비슷하거나 우위. https://arxiv.org/abs/2607.21962
6. ◎ Agent-as-a-Judge: Evaluate Agents with Agents (arXiv:2410.10934, Zhuge 외, 2024-10-14): 요약이 쓰는 "Agent-as-a-Judge"의 원 논문(DevAI 55과제). 요약에는 인용이 없음. https://arxiv.org/abs/2410.10934
7. △ Harness-Bench: Measuring Harness Effects across Models in Realistic Agent Workflows (arXiv:2605.27922): 106개 과제, 최고-최저 하네스 격차 23.8점이라는 스니펫. https://arxiv.org/abs/2605.27922
8. △ HarnessOpt-Bench: Evaluating LLMs at Harness Optimization (arXiv:2608.06301): held-out 평가로 하네스 최적화 능력을 측정. 하네스 진화 평가 논점과 직접 연결. https://arxiv.org/abs/2608.06301
9. △ EvoMemBench: Benchmarking Agent Memory from a Self-Evolving Perspective (arXiv:2605.18421): 자기 진화형 메모리 벤치마크로 검색 결과에 노출. 내용 미열람. https://arxiv.org/abs/2605.18421
10. △ Anthropic, Quantifying infrastructure noise in agentic coding evals (2026-02경): 인프라 설정이 Terminal-Bench 점수를 약 6%p 움직임. 1차 글은 미열람, 2차 요약만 확인. 예: https://www.zenml.io/llmops-database/infrastructure-noise-in-agentic-coding-evaluations
