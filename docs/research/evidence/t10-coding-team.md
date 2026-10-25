# T10 코딩 에이전트 팀 메모리 평가 + 시간 갱신(truth change) 평가 조사

- 조사일: 2026-10-25. 대상 설계: `docs/specs/2026-10-19-memory-eval-harness-design.md` §5(시나리오 계열), §6(모드), §8(지표), §9(기준선), §11(Open-SWE-Traces 생성).
- 검증 방식: arXiv 초록 페이지, Hugging Face 데이터셋 카드와 HF API, 벤더 공식 문서를 직접 열어 확인했다. `summary-source.md`는 읽지도 인용하지도 않았다. 검색 결과 요약만 본 항목은 "검색 스니펫"이라고 표시하고 신뢰도를 낮췄다.
- 신뢰도: **높음** = 원문(초록, 카드, 공식 문서)에서 직접 확인. **중간** = 원문이지만 저자나 벤더의 자기 보고이거나, 본문 일부만 확인. **낮음** = 검색 스니펫이나 2차 자료뿐이라 확인 못 함.
- 주의: 2026년 논문 다수가 단일 저자 프리프린트이고 동료 심사 여부가 확인되지 않았다. 표에 "단일 저자"로 적었다. 숫자는 모두 해당 논문의 자기 보고다.

---

## 0. 한 장 요약

1. SWE 에이전트용 메모리 평가는 2026년에 급증했고(VibeMemBench, SWE-ContextBench, DreamBench-SWE), 공통 결론은 **"기억을 저장하는 것보다 맞는 기억을 골라서 쓰는 것이 어렵다"** 이다. VibeMemBench에서는 기존 메모리 시스템 4종 x 해결 모델 3종 조합(표 2에 실린 12개) 중 11개가 메모리를 끈 기준선 이하였다.
2. 틀리거나 무관한 기억의 피해는 실측이 있다. 무관한 텍스트만 넣어도 해결률이 0.7~3.4%p 떨어졌고, 자동 검색한 요약은 기준선보다 4.04%p 낮았다. 다만 **하네스의 규칙 기반 에이전트(§7.1)는 무관한 결과를 건너뛰므로 이 종류의 피해를 거의 재지 못한다.**
3. 규칙 파일(AGENTS.md, CLAUDE.md)은 효과가 작다. 개발자가 쓴 파일은 +4%, LLM이 만든 파일은 -0.5~-2%이고 비용은 약 20% 늘었다. 그래서 **"마크다운 규칙 파일" 기준선은 현실적인 경쟁자이자 하한선**이다.
4. truth change(시간 갱신) 평가는 LongMemEval의 knowledge-update, MemoryAgentBench의 FactConsolidation, Memora의 FAMA, MemStrata/Supersede의 stale-value rate가 있다. 우리 `M1`(틀린 근거로 행동)과 가장 가까운 것은 stale-value rate다. 코딩 도메인에서 실제 이력으로 supersession을 만든 사례는 있으나, **깨끗한 원자적 전이는 실제 수정의 약 18%뿐**이다.
5. 데이터: Open-SWE-Traces(CC BY 4.0)에는 `resolved` 라벨이 있고 실패 궤적도 들어 있지만, **CI 결과 필드와 시각이 없다.** SWE-rebench-V2(CC BY 4.0)와 `instance_id`로 조인하면 `created_at`, `FAIL_TO_PASS`, `test_cmd`를 얻을 수 있다.
6. 개선안 상위 3개: ① `rules_file` 기준선(S), ② SWE-rebench-V2 조인과 다중 모델 중복 검증(M), ③ 이력 길이/잡음 스윕과 "첫 결과 추종" 에이전트(M).

---

## 1. 영역 1: SWE 에이전트의 메모리와 경험 재사용 평가

### 1.1 SWE용 메모리 벤치마크와 경험 재사용 연구

| 출처 (제목, 시점, 링크) | 한 문장 발견 | 신뢰도 | 우리 하네스에 주는 의미 |
|---|---|---|---|
| [VibeMemBench: Evaluating Memory Systems for Coding Agents on Real Repository Coding Tasks](https://arxiv.org/abs/2609.23570), arXiv 2026-09-20 | 111개 목표, SWE-rebench V2 저장소 90개, 이력 궤적 3,634개로 평가했다. 검증된 경험을 직접 주입하면 해결률이 +1.1~+4.5%p(5개 중 4개 모델)였으나, 기존 메모리 시스템 4종이 같은 이력에서 경험을 만들어 검색하면 12개 조합 중 11개가 메모리 끈 기준선을 넘지 못했다. | 높음(초록과 본문 표). 단 단일 벤치 자기 보고 | 저장·검색 품질이 곧 과업 성공이 아님을 보여 준다. M1·M2가 실제 과업 성공을 대리하는지 확인할 **종단 검증 세트**가 필요하다(개선안 6). |
| 같은 논문, [HTML 본문](https://arxiv.org/html/2609.23570v1) | 실패한 조합 231개 중 69.3%는 관련 이력은 찾았지만 수정 내용이 잘리거나 원문 대화에 묻힌 "형태 저하"였고, 그중 40%가 원문을 그대로 저장하는 시스템의 "지시 오염"이었다. 검색 실패(커버리지 16.0%, 순위 1.3%)보다 훨씬 컸다. | 중간(본문 일부만 확인) | 우리 지표는 "관련 결과가 목록에 있는가"까지만 본다(유효 지식 도달률). **결과 본문이 쓸 수 있는 형태인가**는 안 본다. 본문 형태 품질 점검 후보. |
| 같은 논문, [HTML 본문](https://arxiv.org/html/2609.23570v1) | 설계상 누수 통제가 있다. 대상 이전 이력만 쓰고(`history cutoff`), 대상의 정답 패치와 테스트 결과는 경험 생성에 쓰지 않는다. 코드는 [DAMO-ConvAI/VibeMemBench](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/VibeMemBench)에 공개되어 있다고 논문이 밝힌다. | 중간 | 우리 §5.3의 "질문 시점까지 기록된 이벤트만" 규칙과 같은 원칙이다. 시나리오 생성 시 대상 정답 누수 검사(§11 6단계)에 같은 기준을 쓸 수 있다. |
| [SWE-ContextBench: A Benchmark for Context Learning in Coding](https://arxiv.org/abs/2602.08316), arXiv 2026-02, v3 2026-05 | 기초 과업 1,100개 + 관련 과업 376개(51개 저장소, 9개 언어). 정확히 골라 요약한 경험은 정확도와 비용을 개선하나, 필터링 안 되거나 잘못 고른 맥락은 이득이 제한적이거나 부정적이다. (이전 판 요약에는 300+99로 나오므로 버전별 규모 차이 있음) | 높음(초록). 규모는 버전 의존 | "올바른 선택이 핵심"이라는 우리 M1/M2 구조의 외부 근거. |
| 같은 논문, [HTML v3](https://arxiv.org/html/2602.08316v3) (Claude Sonnet 4.5, 관련 과업 99개 Lite) | 무경험 26.26%, 자동 선택한 요약 22.22%(-4.04%p, 패치 실패율 3.03%에서 7.07%), 정답 요약 34.34%(+8.08%p). 정답 요약과 자동 요약의 격차가 12.12%p로, 전체 궤적(1.01%p)보다 훨씬 크다. | 높음(표) | `oracle` 대비 `all_store`의 격차가 곧 핵심 신호라는 근거. 요약형 기억이 틀리게 선택될 때의 피해가 크다. |
| [DreamBench-SWE: A Multi-Session Memory-Hygiene Benchmark for Software Agents](https://arxiv.org/abs/2608.20664), arXiv 2026-08-21 (단일 저자) | 세션 3개로 이뤄진 시퀀스에서 앞 세션이 심은 증거를 3번째 세션의 숨은 오라클로 점검한다. 계열 5개: 규칙 학습, 생성 파일 구분, **낡은 구조 대체**, 리뷰어 선호의 과일반화, **flaky 테스트를 원인으로 오학습**. 사전 등록 v2.1에서 무기억 11.7%, 원문 이벤트 기억 45.6%, Mem0 53.9%. | 높음(초록, HTML) | 우리 계열 틀과 가장 닮은 외부 벤치. 특히 flaky 테스트 오학습은 우리 `coincidence`(우연)와 직접 대응한다. 범위가 큰 선호의 과일반화는 `conditional` 계열의 소재다. 사전 등록 방식도 우리 §12와 같다. |
| 같은 논문, [HTML 본문](https://arxiv.org/html/2608.20664v1) | 진단 지표: UsefulMemoryPrecision, HarmfulMemoryRate, RepeatedErrorRate, RegressionAfterUpdate, ContradictionRepairAccuracy, ScopeAccuracy, stale-use. 그런데 HarmfulMemoryRate는 모든 조건에서 0.000이었고, 저자도 해로운 검색에 대한 주장은 못 한다고 인정한다. 낡은 대체 구간(21칸)은 원문 이벤트 기억 13/21, 하이브리드 8/21로 기술만 한다. | 높음(본문에 명시) | **음성 결과가 중요하다.** 해로운 기억의 비율이 0으로 나오면 지표가 쓸모없다. 우리 M1이 `all_store` 대비 충분히 0 이상이 되도록 변별력 검사(§9.2)를 유지해야 한다는 근거. |
| [SWE-Bench-CL: Continual Learning for Coding Agents](https://arxiv.org/abs/2507.00014), arXiv 2025-06 | SWE-bench Verified를 시간순으로 배열해 망각, 순방향/역방향 전이, 도구 사용 효율을 측정하는 지표를 제안한다. 초록에는 실험 결과 수치가 없다. | 높음(초록). 결과 수치는 확인 못 함 | 시간순 재생이라는 틀은 우리와 같다. 그러나 사실 갱신(supersession)은 다루지 않고, 같은 저장소 안 순서만 쓴다. |
| [From Knowledge to Noise: CTIM-Rover and the Pitfalls of Episodic Memory in Software Engineering Agents](https://arxiv.org/abs/2505.23422), REALM'25 | ExpeL 방식의 저장소별 에피소드 메모리를 AutoCodeRover에 붙였더니 어떤 설정에서도 기준 에이전트를 넘지 못했다. 저자는 산만한 기억 항목이나 예시 궤적의 잡음을 원인으로 본다. | 높음(초록). 수치 없음 | 잡음이 이득을 지우는 사례. 잡음 스윕(개선안 3)의 근거. |
| [SWE-Exp: Experience-Driven Software Issue Resolution](https://arxiv.org/abs/2507.23361), arXiv 2025-07 | 성공/실패 수리 시도를 모두 담은 경험 은행으로 SWE-bench Verified에서 Claude 4 Sonnet 기준 73.0%를 보고한다. 초록에 악영향 분석은 없다. | 중간(저자 자기 보고) | 실패 시도도 저장한다는 점은 우리 `failure_feedback` 계열과 맞닿는다. 악영향 평가가 없다는 사실 자체가 공백. |
| [Agent Workflow Memory](https://arxiv.org/abs/2409.07429), arXiv 2024-09 / [ExpeL](https://arxiv.org/abs/2308.10144), AAAI-24 | AWM은 웹 과업에서 상대 성공률이 Mind2Web +24.6%, WebArena +51.1%이고 훈련-시험 분포 차이가 커질수록 이득이 커진다. ExpeL은 경험에서 자연어 통찰을 추출해 가중치 갱신 없이 쓴다. SWE 평가는 아님. | 높음(초록) | "경험 재사용"의 고전 기준. SWE에는 직접 대응 평가가 없고, 위 CTIM-Rover가 ExpeL의 SWE 이식 실패 사례다. |
| [Procedural Memory Under Change: Reuse and Interference in Controlled Web Tasks](https://arxiv.org/abs/2609.09774), arXiv 2026-09 (단일 저자) | 절차 기억이 더 이상 맞지 않는 불일치 4종, 32칸에서 사전 정의한 간섭 징후는 나타나지 않았다. 현재 과업 증거가 명시적이고 충분할 때는 낡은 절차가 큰 해를 주지 않는다고 하되 일반화는 주장하지 않는다. | 높음(초록). 소규모, 7-8B급 모델 | **반례도 있다.** 낡은 기억이 항상 해로운 것은 아니며, 현재 증거가 분명하면 무시된다. 그래서 우리 `truth_change`의 block 문항은 "현재 증거가 약한" 상황에서도 만들어야 변별된다. |
| [SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks](https://arxiv.org/abs/2602.12670), arXiv 2026-02, v4 2026-06 | 큐레이션된 스킬이 평균 통과율을 33.9%에서 50.5%(+16.6%p)로 올렸다(87개 과업, 8개 영역, 18개 모델-하네스 조합). 모듈 3개 이하의 집중된 스킬이 방대한 문서보다 낫다. | 높음(초록). 영역별/자가생성 결과는 확인 못 함 | 절차 지식 라이브러리는 "짧고 집중된 것"이 낫다는 근거. 규칙 파일 기준선도 길이를 제한해야 공정하다(개선안 1). |

### 1.2 팀 공유 맥락: 규칙 파일, 벤더 기능, 공유 메모리

| 출처 | 한 문장 발견 | 신뢰도 | 우리 하네스에 주는 의미 |
|---|---|---|---|
| [Claude Code docs: How Claude remembers your project](https://code.claude.com/docs/en/memory) | CLAUDE.md는 "강제 설정이 아니라 맥락"으로 로드되고, 지시가 서로 충돌하면 임의로 하나를 고를 수 있으며, 파일당 200줄 이하를 권한다. 자동 메모리는 시작 시 `MEMORY.md`의 처음 200줄/25KB만 읽고 **기기 로컬**이다(기기 간 공유 안 됨). 프로젝트 CLAUDE.md는 버전 관리로 팀과 공유된다. | 높음(공식 문서) | 팀 공유의 현실적인 형태 = 버전 관리되는 마크다운 + 길이 제한 + 충돌 시 임의 선택. 이것이 `rules_file` 기준선의 규격이 된다. 자동 메모리가 기기 로컬이라는 점은 "팀 메모리"가 아직 벤더 기본 기능이 아님을 뜻한다. |
| 같은 문서의 `/doctor prompt-audit` 설명 | 낡거나 충돌하는 지시, 없는 파일/명령 참조를 찾는 점검 기능을 벤더가 직접 제공한다. 변경은 사용자가 요청할 때만 적용된다. | 높음(공식 문서) | 낡은 규칙이 실제 문제라는 벤더 측 인정. 다만 이 기능의 정확도에 대한 독립 평가는 이번 검색에서 찾지 못했다. |
| [Building an agentic memory system for GitHub Copilot](https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/), GitHub 블로그 2026-01 | 기억을 코드 위치 인용과 함께 저장하고 **사용 시점에 현재 브랜치와 대조 검증**한다. 거짓 기억을 심은 저장소 실험에서 에이전트가 모순을 잡아 고쳤다고 한다. 코딩 에이전트 A/B에서 PR 병합률 83%에서 90%, 코드 리뷰 긍정 피드백 75%에서 77%. | 중간(벤더 자기 보고, 독립 재현 없음) | **"인용 + 사용 시점 검증"은 낡은 규칙에 대한 벤더의 해법**이다. 우리에게는 새 계열 `implicit_invalidation`(코드 변경으로 규칙이 암묵적으로 무효화됨)과 앵커 필드 아이디어(개선안 4)의 근거. |
| [Copilot memory 공식 문서](https://docs.github.com/en/copilot/concepts/agents/copilot-memory) | 저장소 단위 사실은 그 저장소 접근 권한이 있는 팀원에게 공유되고, 개인 선호는 공유되지 않는다. 28일간 쓰지 않으면 삭제되며 사용 시 갱신된다. 저장소 소유자가 삭제할 수 있다. | 높음(공식 문서) | 우리 `scope: personal | team`, 누출 계열과 같은 구조. **저장소 경계**(repo-scoped)도 별도 격리 단위라는 점이 우리 §5.5에 없다(개선안 5의 `leak_cross_repo`). |
| [Cursor Rules 문서](https://cursor.com/docs/context/rules) | 프로젝트 규칙(`.cursor/rules`), 사용자 규칙, 팀 규칙(대시보드 관리, 강제 여부 설정 가능), AGENTS.md가 있고 우선순위는 팀 규칙 > 프로젝트 규칙 > 사용자 규칙이다. 에이전트가 `/create-rule`로 규칙 파일을 자동 생성할 수 있다. 이 페이지에는 Memories 언급이 없고, 이전 Memories 문서 주소([docs.cursor.com/context/memories](https://docs.cursor.com/context/memories))는 `cursor.com/docs`로 영구 리디렉션된다. | 높음(문서). "Memories 기능 제거" 자체는 확인 못 함(낮음) | 규칙 우선순위 `팀 > 프로젝트 > 사용자`는 `truth_change`에서 "누가 말했는가"를 신호 가중으로 쓰는 시나리오의 근거. 에이전트가 만든 규칙이 규칙 파일로 되먹임되는 구조는 `echo` 계열과 같다. |
| [Collaborative Memory: Multi-User Memory Sharing in LLM Agents with Dynamic Access Control](https://arxiv.org/abs/2505.18279), arXiv 2025-05 | 개인/공유 2계층, 조각별 출처 메타데이터, 시간에 따라 변하는 권한 정책을 읽기/쓰기 정책으로 구현하는 프레임워크를 제안한다. 초록에서 확인한 평가 수치는 없다. | 높음(초록). 실험은 확인 못 함 | 누출 평가는 설계 논문만 있고 **독립 벤치마크는 이번 검색에서 찾지 못했다**(낮음~중간). 우리 `leak_*` 계열이 차별점이 될 수 있다. |

### 1.3 규칙 파일(AGENTS.md 등)의 실측 효과와 낡은/틀린 규칙의 영향

| 출처 | 한 문장 발견 | 신뢰도 | 의미 |
|---|---|---|---|
| [Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?](https://arxiv.org/abs/2602.11988), arXiv 2026-02 | 맥락 파일은 대체로 성공률을 올리지 못하고 비용만 20% 넘게 올렸다. | 높음(초록) | 규칙 파일 기준선이 강한 경쟁자가 아닐 수 있다. 그러나 아래 수치와 함께 봐야 한다. |
| 같은 논문, [HTML v1](https://arxiv.org/html/2602.11988v1) | SWE-bench Lite에서 LLM 생성 파일 -0.5%, AGENTbench(12개 저장소, 138건)에서 -2%. 개발자가 작성한 파일은 AGENTbench에서 +4%. 비용은 LLM 생성 약 +20~23%, 개발자 작성 최대 +19%. 문서를 모두 지우면 LLM 생성 파일이 +2.7%로 좋아진다(문서와 중복되기 때문). | 중간(HTML v1, 버전에 따라 수치가 다를 수 있음) | 규칙 파일의 가치는 "저장소 문서에 없는 비표준 관행"에 있다. 우리 시나리오 사실도 `README로 알 수 있는 것`이 아닌 **팀 고유 규칙**이어야 규칙 파일 대비 엔진의 가치를 잴 수 있다. 시나리오 사실 후보 추출(§11 2단계)에 "공개 문서로 유추 가능" 필터가 필요하다. |
| [Do Context Files Help Coding Agents? A Two-Agent Ablation Study on Real Repositories](https://arxiv.org/abs/2607.27250), arXiv 2026-07 (단일 저자) | Claude Code와 Codex에서 17개 과업, 3개 저장소, 288회 실행으로 보니 맥락 전략이 정확도를 측정 가능한 수준으로 바꾸지 못했다(동등성 검정으로 약 10~15%p 이내). | 높음(초록). 표본 작음 | 효과가 작아 검정력이 낮다는 것도 시사한다. 우리 하네스가 결정적인 규칙 기반 에이전트를 쓰는 이유(§7.1)를 뒷받침한다. |
| [Agent READMEs: An Empirical Study of Context Files for Agentic Coding](https://arxiv.org/abs/2511.12884), arXiv 2025-11, v2 2026-08 | 저장소 1,925개의 맥락 파일 2,303개를 분석했다. 테스트 절차 75.9%, 구현 세부 70.8%, 아키텍처 68.1%가 많고 보안(14.8%)과 성능(14.5%)은 드물다. 파일은 "빈번한 작은 추가"로 진화한다. | 높음(초록) | 시나리오 사실 유형 분포의 현실 근거: 빌드/테스트 명령, 구현 규칙, 아키텍처 순. 초록에는 낡음의 빈도 수치가 없다. |

> **공백:** "틀리거나 낡은 규칙을 일부러 넣은 맥락 파일"의 영향을 직접 측정한 연구는 이번 검색에서 찾지 못했다(위 AGENTS.md 논문도 의도적으로 틀린 지시를 넣는 실험은 하지 않았다고 확인됨). 벤더 측만 대응책(검증, 점검)을 낸다. 우리 `truth_change`/`implicit_invalidation`이 이 공백을 채울 수 있다. 신뢰도: 중간(부재 주장).

### 1.4 우리 계열 틀과 외부 근거의 대응 (공백 표시)

| 우리 계열 (§5.3) | 외부 근거 | 공백 |
|---|---|---|
| `normal_repeat` | VibeMemBench, SWE-ContextBench(정확히 고르면 이득) | 없음 |
| `coincidence`, `single_source_poison` | DreamBench-SWE의 flaky 오학습, [Xiong 외](https://arxiv.org/abs/2505.16067)의 오류 전파 | **출처 독립성(독립 출처 2곳 이상)을 요구하는 평가는 찾지 못함 = 우리 차별점** |
| `echo` | Xiong 외: 틀린 기록이 메모리에 저장되면 다음 과업으로 번진다. [본문](https://arxiv.org/html/2505.16067v2)에서 무차별 추가가 4개 에이전트 모두에서 고정 메모리보다 낮았다 | SWE 도메인 측정은 없음 |
| `truth_change` | 2장 전체 | SWE 이력에서 만든 사례는 MemStrata 후속 하나뿐(2.3) |
| `conditional` | DreamBench의 범위 변형, Copilot 저장소 단위 격리 | 환경/버전 조건별 규칙 평가는 찾지 못함 |
| `workaround_then_root` | 가까운 것: DreamBench의 flaky 오학습 | **임시조치 대 근본 수정을 구분하는 평가는 찾지 못함** |
| `failure_feedback` | Xiong 외 v2: 환경 피드백이 오류 전파를 줄인다 | SWE 도메인 측정은 없음 |
| `leak_*` | Collaborative Memory(설계), Copilot 범위 규칙 | 독립 누출 벤치마크 찾지 못함 |

---

## 2. 영역 2: 시간 갱신(truth change)과 충돌 평가

### 2.1 범용 메모리 벤치마크의 갱신/시간 범주

| 출처 | 한 문장 발견 | 신뢰도 | 의미 |
|---|---|---|---|
| [LongMemEval](https://arxiv.org/abs/2410.10813), ICLR 2025 | 500문항, 능력 5개(정보 추출, 다중 세션 추론, 시간 추론, 지식 갱신, 기권). 상용 챗 어시스턴트와 장문맥 모델은 지속 대화에서 30% 정확도가 떨어진다. | 높음(초록) | 범주 이름과 구성을 참고. |
| [LongMemEval HTML](https://arxiv.org/html/2410.10813) | 지식 갱신은 "사용자 삶의 상태 변화"를 알아채는 능력이다. 시간 추론은 타임스탬프 메타데이터와 명시적 시간 표현을 둘 다 시험한다. 기권 문항 30개는 다른 문항을 거짓 전제로 바꿔 만든다. 채점은 GPT-4o 판정이고 사람과 97% 넘게 일치한다고 보고한다. | 중간(본문 일부) | **기권(거짓 전제) 문항**은 우리 `abstain` 처리(§7.3)와 같은 발상이다. 시간 메타데이터를 쓰는 문항 vs 명시적 시간 표현 문항을 구분하는 것은 우리 `valid_from/until`이 가상 시계(`tick`)로 해결하는 문제다. |
| [Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions (MemoryAgentBench)](https://arxiv.org/html/2507.05257v3) | FactConsolidation은 MQuAKE의 반사실 편집 쌍(원래 사실과 모순되는 다시 쓴 사실)을 순번을 붙여 이어 붙여 만든다. 정확도: GPT-4o 장문맥 60.0(단일 홉)/5.0(다중 홉), BM25 RAG 48.0/3.0, Mem0 18.0/2.0, MemGPT 28.0/3.0, MIRIX 14.0/2.0. 모든 방식의 다중 홉이 약 7% 이하. | 높음(표) | "최신 사실을 우선하라"고 **프롬프트로 알려 줘도** 거의 모든 메모리 방식이 다중 홉에서 실패한다. 우리는 프롬프트 지시 없이 엔진이 알아내야 하므로 더 어렵다. |
| [Memora: From Recall to Forgetting: Benchmarking Long-Term Memory for Personalized Agents](https://arxiv.org/abs/2604.20006), ACL 2026 Findings | FAMA(Forgetting-Aware Memory Accuracy)는 무효화된 기억에 의존하면 감점한다. 모델 4종과 메모리 에이전트 6종에서 무효 기억 재사용이 잦았고 메모리 에이전트의 개선은 미미했다. | 높음(초록). 식은 검색 스니펫(중간): `FAMA = max(0, MPA − λ(1 − FAA))`, 유효 기억 포함 점수 MPA와 무효 정보 배제 점수 FAA를 합성하며 판정은 LLM 다수결 | 우리 M1(틀린 것을 씀)과 M2(맞는 것을 씀)가 사실상 FAMA의 두 항이다. 우리는 **분리 보고 + 결정적 채점**이므로 LLM 판정 편향이 없다는 점을 장점으로 쓸 수 있다. |
| [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956), arXiv 2025-01 | Graphiti 기반 Zep이 DMR에서 94.8%(MemGPT 93.4%), LongMemEval에서 최대 18.5% 정확도 향상, 응답 지연 90% 감소를 주장한다. 모순된 사실에 `invalid_at`을 찍고 삭제하지 않는 이중 시간 모델이다. | 중간(벤더 자기 보고, 독립 재현 없음) | 어댑터로 붙일 Graphiti(§10)의 주장 수치. 우리 하네스가 **독립 검증 지점**이 된다. 무효화된 엣지를 건넬지는 Graphiti 기본값을 따르고 기록한다는 §10 결정이 곧 이 평가의 핵심 변수다. |

### 2.2 truth change를 만드는 방법(valid-from/valid-until)과 지표

| 출처 | 구성 방식 | 지표 | 신뢰도 | 의미 |
|---|---|---|---|---|
| [Ground Truth First: A Longitudinal Evaluation Instrument for Agent Memory](https://arxiv.org/abs/2607.21962), arXiv 2026-07 (단일 저자) | 날짜가 붙은 사실을 먼저 시드 샘플러로 만들고, 그 뒤에 LLM이 대화/이메일 본문을 렌더링하며, 검증기가 모든 사실이 본문에 있는지 확인하고, 질문은 기계적으로 생성한다. 사실마다 유효 구간이 있고 시점 기준(as-of) 질문과 주입 탐침이 있다. | 정답은 "스크립트로부터 구성상 유효". 기억 시스템 5종 + 무기억 대조를 3주와 9주 두 기간으로 비교하니 순위가 뒤집혔다(예산 제한 큐레이션 지도는 3주에 우수하나 9주에 96%에서 72%로 하락). | 높음(초록). 합성 코퍼스 약 380문항 | **우리 §11 원칙("구조는 코드, 내용은 LLM")과 같은 설계**다. 이력이 길어질 때 순위가 바뀌므로 이력 길이 스윕이 필요하다(개선안 3). |
| [Temporal Validity in Retrieval Memory (MemStrata)](https://arxiv.org/abs/2606.26511), arXiv 2026-06 (단일 저자) | 사실을 (주어, 관계, 객체) 단위로 저장하고, 새 사실이 기존 값과 모순되면 결정적 규칙으로 옛 값을 이중 시간 원장에서 은퇴시킨다. 코사인 유사도로 모순과 중복을 가르는 AUROC가 0.59로 거의 우연 수준이다. | 진화하는 지식에서 정확도 MemStrata 0.95~1.00 대 RAG 0.20~0.47. 답을 강제할 때 stale 값 제공률 RAG 15~40%, MemStrata 약 0%. | 중간(저자가 제안한 시스템 자기 평가) | 임베딩 검색은 옛 값과 새 값을 구분하지 못한다는 점은 `all_store`(BM25)가 `truth_change`에서 M1이 높게 나와야 한다는 §9.2 자가 검증 조건과 일치한다. |
| [Temporal Validity on Real Software Histories](https://arxiv.org/abs/2608.20685), arXiv 2026-08 (단일 저자) | SWE-bench Lite/Verified의 실제 이슈 707개에서 **깨끗한 원자적 상태 전이 130개**(실제 수정의 약 18%)를 뽑아, 값만 다른 stale/current 문장 쌍으로 만든다. | 정확도, stale 값 제공률(강제 응답 시), 지연. RAG 정확도 0.57~0.59에 stale 제공률 36~38%, MemStrata 0.91에 약 0%. LLM 재순위기는 stale 제공을 줄이지 못했다. | 중간(저자 자기 평가). 데이터/코드 공개 여부는 확인 못 함 | **코딩 도메인 truth change를 실제 이력에서 만든 유일한 사례.** 실제 수정의 82%는 원자적 전이가 아니므로, `truth_change` 사실은 "이름 변경, 엔드포인트 이동, 의존성 버전 올림" 같은 **깨끗한 전이 유형**으로 한정해 생성하는 것이 현실적이다. |
| [Supersede: Diagnosing and Training the Memory-Update Gap in LLM Agents](https://arxiv.org/abs/2606.27472), arXiv 2026-06 (단일 저자) | LongMemEval의 knowledge-update 부분집합에서, 전체 문맥은 약 92%인데 제한된 자가 유지 메모리는 77%로 떨어진다(gpt-5.4). 대화가 24배 늘면 정확도가 68%에서 28%로 하락한다. 현재값 보상/옛 값 벌점의 RL 환경을 공개한다. | 현재값 정확도, 쌍체 McNemar 검정 | 중간(저자 자기 보고) | 병목은 이해가 아니라 **메모리 유지**라는 결론이다. 이력 길이에 따른 하락은 이력 길이 스윕의 근거. |
| MQuAKE 기반 지식 편집 평가([MQuAKE 논문](https://arxiv.org/abs/2305.14795), EMNLP 2023) | 반사실 편집과 시간 편집 부분집합. 지표: 편집 단위 성공률(편집한 사실 회상), 사례 단위 정확도(다중 홉 사례 전체 회상), 다중 홉 정확도(문장 변형 3개 중 하나라도 맞으면 정답). | 위 지표 정의는 검색 스니펫 기반 | 낮음~중간 | 지식 편집(모델 가중치)과 메모리(외부 저장)는 다른 문제이므로 지표를 그대로 가져오지는 않는다. 다만 "편집한 사실 직접 질문" 대 "그 사실에 의존한 다중 홉 질문"의 구분은 우리 `truth_change`에서 **간접 질문 문항**(f_new에서 파생된 질문)을 추가하는 아이디어가 된다(개선안 4의 부산물). |

### 2.3 지표 비교

| 개념 | 다른 연구 | 우리 하네스 (§8) | 비고 |
|---|---|---|---|
| 갱신 성공 | 현재값 정확도(Supersede, MemStrata) | M2(참인 근거로 행동) + 유효 지식 도달률 | 우리는 "답"이 아니라 "근거를 쓴 행동"으로 채점해 LLM 판정 불필요 |
| stale 답 비율 | stale 값 제공률(MemStrata), stale-use(DreamBench) | **M1**(`block` 문항에서 틀린 근거로 행동), 오염 노출률 | 이름 대응이 명확. 보고서에서 "M1 = stale/틀린 근거 행동률"이라고 병기하면 외부 비교가 쉽다 |
| 지연 | 없음(찾지 못함) | 철회 지연, 반영 지연 | **우리가 앞서 있는 지표.** 실제 외부 연구에서 철회 지연을 직접 재는 사례는 이번 검색에서 찾지 못함 |
| 과거 시점 질문 | as-of 질문(Ground Truth First) | `promote`(D일 이전 맥락) | 같은 개념 |
| 망각/무효 기억 배제 | FAA 항(Memora) | `block` | 같은 개념 |

---

## 3. 영역 3: 시나리오 소스로 쓸 오픈 트레이스 데이터

### 3.1 데이터셋 비교

| 데이터셋 (링크) | 라이선스 | 규모 | 형식과 라벨 | 반복 실패 | CI 결과 | 신뢰도 |
|---|---|---|---|---|---|---|
| [nvidia/Open-SWE-Traces](https://huggingface.co/datasets/nvidia/Open-SWE-Traces) (논문: [arXiv 2606.16038](https://arxiv.org/abs/2606.16038)) | CC BY 4.0([카드](https://huggingface.co/datasets/nvidia/Open-SWE-Traces/raw/main/README.md)). 논문 페이지에는 데이터 라이선스 명시 없음(원 저장소는 MIT/Apache/BSD 필터) | 구성(config) 4개: v1.0 151,219건, v1.1 263,226건, v1.2, v2.0(약 169k)([분포표](https://huggingface.co/datasets/nvidia/Open-SWE-Traces/raw/main/data_distribution.md)). 언어 9개, v1.0 Python은 34,343건 | 필드: `instance_id`, `repo`, `trajectory_id`, `messages`, `tools`, `resolved`(1/0/-1), `metadata`(분류, 교사 모델, 참조/모델 패치). 하네스 분할: OpenHands, SWE-agent, mini-SWE-agent, OpenCode, Claude Code. 날짜/시각 필드 없음 | `resolved=0` 궤적이 많다. v1.0 표에서 해결/미해결/미상이 모두 큰 수. 같은 PR당 궤적이 여러 개(예: OpenHands x MiniMax 조합은 PR 19,022개에 궤적 43,603건) | **전용 CI/테스트 필드 없음.** `resolved` 한 칸뿐 | 높음(카드, 분포표) |
| 같은 데이터 직접 확인 | - | v2.0 `claudecode` 분할 첫 100행 표본 | 시스템 프롬프트가 "Claude Agent SDK, CWD /testbed". 100행 모두 Python, 해결 61/미해결 39. 100행 안에 `instance_id` 중복 없음([datasets-server API](https://datasets-server.huggingface.co/rows?dataset=nvidia/Open-SWE-Traces&config=v2.0&split=claudecode&offset=0&length=100)) | - | - | 중간(100행 표본, 본인 측정) |
| [nebius/SWE-rebench-V2](https://huggingface.co/datasets/nebius/SWE-rebench-V2) (논문 [arXiv 2602.23866](https://arxiv.org/abs/2602.23866)) | CC BY 4.0([HF API](https://huggingface.co/api/datasets/nebius/SWE-rebench-V2)) | 과업 약 32k, 언어 20개, 저장소 약 3.6k(기술 보고서, [Nebius 블로그](https://nebius.com/blog/posts/meet-swe-rebench-v2) 검색 스니펫) | 필드: `base_commit`, **`created_at`**, `instance_id`, `repo`, `problem_statement`, `patch`, `test_patch`, **`FAIL_TO_PASS`**, `PASS_TO_PASS`, `install_config`(테스트 명령 포함)([필드 목록](https://datasets-server.huggingface.co/first-rows?dataset=nebius/SWE-rebench-V2&config=default&split=train)) | 궤적 없음(과업만) | **테스트 지정은 있음(실행 결과가 아니라 지정)** | 높음(필드, 라이선스). 규모는 중간 |
| [nebius/SWE-rebench-V2-PRs](https://huggingface.co/datasets/nebius/SWE-rebench-V2-PRs) | CC BY 4.0([카드](https://huggingface.co/datasets/nebius/SWE-rebench-V2-PRs/raw/main/README.md)) | 10만 건 이상 규모 구간 | `created_at`, `pr_description`, `pr_author`, `pr_labels` 포함 | 궤적 없음 | 테스트 지정 | 높음 |
| [nebius/SWE-rebench-openhands-trajectories](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories) | CC BY 4.0 | 67,074건, 저장소 1,823개, 성공 32,161건, 평균 64.3턴(Qwen3-Coder-480B + OpenHands 0.54.0)([카드](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories/raw/main/README.md)) | 메시지 목록 | 실패 궤적 34,913건 | 없음 | 높음 |
| [nebius/SWE-agent-trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories) | CC BY 4.0([API](https://huggingface.co/api/datasets/nebius/SWE-agent-trajectories)) | 80,036건 중 성공 13,389건([비교표](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories/raw/main/README.md)) | 2024-12 갱신, 부트스트랩 모델 Qwen2.5-72B/Llama3-70B | 성공률 낮아 실패 풍부 | 없음 | 중간(2차 표) |
| [SWE-bench/SWE-smith-trajectories](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories) | MIT([API](https://huggingface.co/api/datasets/SWE-bench/SWE-smith-trajectories)) | 49,897건 중 성공 21,513건(합성 과업)([비교표](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories/raw/main/README.md)) | SWE-agent | 있음 | 없음 | 높음(라이선스), 중간(규모) |
| [togethercomputer/CoderForge-Preview](https://huggingface.co/datasets/togethercomputer/CoderForge-Preview) | 카드에 단일 라이선스 없음. 행마다 `license` 열이 다름(Apache-2.0, MIT 등 9종) | 826,556행, 72.8GB, 과업 약 51k | `reward`(-1~1), 같은 과업의 `run1..run8` 형태 ID로 같은 과업 여러 번 실행 흔적 | 있음(`reward` 낮은 궤적) | 없음 | 높음(카드), 반복 실행은 추정(카드가 직접 말하지 않음) |
| [nvidia/SWE-Hero-openhands-trajectories](https://huggingface.co/datasets/nvidia/SWE-Hero-openhands-trajectories), [SWE-Zero](https://huggingface.co/datasets/nvidia/SWE-Zero-openhands-trajectories) | CC BY 4.0([API](https://huggingface.co/api/datasets/nvidia/SWE-Hero-openhands-trajectories)) | Hero는 1만~10만, Zero는 10만~100만 규모 구간(arXiv 2604.01496 표기) | OpenHands | 내용 미확인 | 미확인 | 낮음(카드 본문은 열어 보지 않음) |
| [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat) (논문 [arXiv 2604.20779](https://arxiv.org/abs/2604.20779), COLM 2026) | `odc-by`, **접근 제한(gated, 자동 승인)**([HF API](https://huggingface.co/api/datasets/SALT-NLP/SWE-chat)) | 약 18,000세션, 사용자 프롬프트 22.9만 건, 도구 호출 약 200만 건(v2 초록) | **실제 개발자**의 공개 저장소 세션(Claude Code, Codex, Gemini CLI, Cursor 등). 사용자가 에이전트 출력에 반박하는 비율 50%, 에이전트 코드의 59%만 커밋에 남음 | 사용자 정정/실패 보고가 풍부(구조적 라벨 여부 확인 못 함) | 직접 필드는 확인 못 함 | 높음(초록) |
| [Azure GitHub Copilot Coding Agent Traces 2026](https://github.com/Azure/AzurePublicDataset/blob/master/GitHubCopilotCodingAgentDataset2026.md) | CC-BY | 세션 약 30만 건, 2026-06-01~07 | **메타데이터만**: 코드, diff, 프롬프트, 응답, 저장소 이름, 파일 경로 없음 | 판단 불가 | 없음 | 높음 |
| GitHub Actions 로그 데이터: [Zenodo 116k 워크플로 실행](https://zenodo.org/records/10154920/latest), [Flaky builds 연구](https://arxiv.org/abs/2602.02307) | Zenodo 라이선스는 확인 못 함 | Zenodo는 25k 프로젝트, 143.4GB(검색 스니펫). Flaky는 Java 1,960개 프로젝트 | 실행 메타데이터와 로그 | flaky 실패 15개 유형 분류 | **있음(실제 CI)** | 낮음~중간(검색 스니펫) |

### 3.2 설계 문서 §11과의 대조 (확인해야 할 가정)

| §11/§5.3의 가정 | 확인 결과 | 조치 |
|---|---|---|
| Open-SWE-Traces는 CC BY 4.0 | 카드에 CC BY 4.0 ([카드](https://huggingface.co/datasets/nvidia/Open-SWE-Traces/raw/main/README.md)) | 일치. 출처 표기(attribution)만 시나리오 `provenance`에 남기면 된다 |
| "Python 저장소만 남긴다" | `language` 필드가 있어 필터 가능 | 일치 |
| "같은 `instance_id`를 여러 모델이 푼 묶음" | v1.0에 모델 2개 x 하네스 2개, PR당 궤적이 2개 이상([분포표](https://huggingface.co/datasets/nvidia/Open-SWE-Traces/raw/main/data_distribution.md))이어서 가능성은 높다. 그러나 같은 `instance_id`가 모델 간에 겹치는 비율은 **직접 확인하지 못했다**(HF 필터 API가 인증 오류) | 생성 전에 중복 비율 통계를 먼저 내는 단계 추가 |
| 모델 하나를 사용자, 궤적 하나를 세션으로 대응 | 같은 계열 모델(Qwen3.8 등)의 궤적은 독립 출처로 보기 어렵다 | "독립 출처"는 **서로 다른 모델 계열 + 서로 다른 하네스** 조합으로 한정 |
| `signal: test`/`human` 이벤트 | 데이터에 테스트/사람 신호 없음. `resolved`와 패치만 있음 | `resolved`에서 합성하고 `provenance.author: generator`에 명시. 사람 신호 문구는 SWE-chat에서 참고(gated) |
| 시각(`t`) | Open-SWE-Traces에 날짜 없음 | SWE-rebench-V2의 `created_at`을 `instance_id`로 조인([카드 필드](https://huggingface.co/datasets/nebius/SWE-rebench-V2-PRs/raw/main/README.md)) |
| 데이터 품질 | v1.0에서 **git 해킹 행동 궤적을 정리**해 207k에서 151k로 줄였다([카드](https://huggingface.co/datasets/nvidia/Open-SWE-Traces/raw/main/README.md)). 중간 라벨 `-1`(미상)이 많다 | `resolved=-1`은 제외 |
| 카드 날짜 | v2.0 릴리스가 카드에 "10/26"으로 적혀 있고 HF 수정 시각은 2026-10-23이다([API](https://huggingface.co/api/datasets/nvidia/Open-SWE-Traces)) | 카드 표기 오류일 수 있음. 버전 고정(커밋 sha)으로 재현성 확보 |

---

## 4. 영역 4: "틀린 기억에 따라 행동했을 때"의 피해 측정

| 출처 | 측정 | 수치 | 신뢰도 | 의미 |
|---|---|---|---|---|
| [VibeMemBench](https://arxiv.org/html/2609.23570v1) | 이력 주입이 해결률을 오히려 낮춘 비율 | 검색-조합 1,332개 중 26.2%가 해결률을 낮췄고 순손실은 127 시드. 메모리 끈 기준선이 4/4일 때 42.0%는 잃기만 할 수 있었다. **무관 텍스트 대조군**은 해결률을 0.7~3.4%p 낮췄다. 같은 기록이 약한 모델에는 도움이 되고 강한 모델에는 해가 된 경우가 비교 가능한 118건 중 64건. 334줄짜리 기록 하나가 aiohttp-7907 과업을 4/4에서 0/4로 떨어뜨렸다. | 중간(본문 일부 확인) | **무관 맥락 대조군**이 표준 설계로 쓰인다. 우리 규칙 기반 에이전트는 무관 결과를 건너뛰므로 이 피해가 안 보인다. |
| [SWE-ContextBench](https://arxiv.org/html/2602.08316v3) | 자동 선택 요약의 효과 | 무경험 대비 -4.04%p, 패치 실패율 3.03%에서 7.07% | 높음 | 요약형 기억의 오선택 피해. |
| [How Memory Management Impacts LLM Agents (Xiong 외)](https://arxiv.org/html/2505.16067v2), arXiv 2025-05, v2 2025-10 | 무차별 추가 대 선택적 추가 | EHRAgent 정확도 고정 메모리 16.75, 전부 추가 13.05, 엄격 선택 38.50. 4개 에이전트(비-SWE) 모두에서 "전부 추가"가 고정 메모리보다 낮았다(예: CIC-IoT 71.50에서 59.90). 삭제 전략을 섞으면 메모리 크기도 줄었다(EHRAgent 248건). | 높음(표). SWE 도메인 아님 | `all_store`(전부 저장)가 `none`보다 나빠질 수 있다는 외부 근거. 우리 `all_store`가 M1이 최대여야 한다는 §9.2 조건과 부합. |
| [CTIM-Rover](https://arxiv.org/abs/2505.23422) | ExpeL식 에피소드 메모리의 SWE 적용 | 어떤 설정에서도 기준 에이전트를 넘지 못함. 원인을 산만한 항목/예시 궤적의 잡음으로 추정 | 높음(초록). 수치 없음 | 위와 같음. |
| [Large Language Models Can Be Easily Distracted by Irrelevant Context](https://arxiv.org/abs/2302.00093), ICML 2023 | 산술 문제에 무관한 문장을 끼운 GSM-IC | 성능이 "극적으로" 하락(초록에 수치 없음) | 높음(방향), 수치는 확인 못 함 | 일반적 근거. SWE 직접 증거는 위 3개. |
| [DreamBench-SWE](https://arxiv.org/html/2608.20664v1) | 낡음/오학습/과범위 | stale-use 풀링 756/3780(0.200), 그러나 원인 귀속은 안 함. HarmfulMemoryRate는 모든 조건 0.000 | 높음 | 해로운 검색을 분리해 재는 것은 이 연구에서도 실패. **측정이 어렵다는 점 자체가 시사점**. |
| [Procedural Memory Under Change](https://arxiv.org/abs/2609.09774) | 불일치 절차의 간섭 | 소규모 통제에서 간섭 징후 없음 | 높음(초록). 일반화 불가 | 반례. 현재 증거가 분명하면 낡은 절차가 무시됨. |
| [Evaluating AGENTS.md](https://arxiv.org/html/2602.11988v1) | 불필요한 요구의 영향 | 에이전트가 지시를 잘 따라 추론 토큰이 20~22% 늘고 성공은 오르지 않음. 의도적으로 틀린 지시 실험은 없음 | 중간 | 지시를 **잘 따른다는 것**이 곧 틀린 지시의 위험이다. |

**의미 정리:**
- 해로운 기억의 피해는 방향과 크기가 일관된다: 무관 맥락 -0.7~-3.4%p(VibeMemBench), 자동 선택 요약 -4.04%p(SWE-ContextBench), 무차별 저장은 비-SWE 4종에서 모두 하락(Xiong 외).
- 하네스의 규칙 기반 에이전트(§7.1: "관련 있는 첫 결과를 따른다")는 **무관 결과를 건너뛴다.** 따라서 위 피해 중 "무관 맥락이 성능을 깎는" 경로는 컨텍스트 크기 관찰 지표(§8.1 성능)로만 간접 반영된다. 이것이 가장 큰 측정 공백이다.

---

## 5. 개선 아이디어 6개 (우선순위 순)

| 순위 | 아이디어 | 노력 | 근거 | 무엇을 바꾸나 |
|---|---|---|---|---|
| 1 | **`rules_file` 기준선 추가**: 팀이 볼 수 있는 `human`/`test` 신호 이벤트(또는 에이전트 기록)를 시간순으로 이어 붙인 평범한 마크다운 1개를, 질문과 무관하게 최신 200줄만 결과로 건넨다(Claude Code 권장 상한). 두 변형: (a) 줄마다 출처 id 포함, (b) 출처 없음(마커로 채점). | S | [Claude Code docs](https://code.claude.com/docs/en/memory)(200줄, 충돌 시 임의 선택), [AGENTS.md 논문](https://arxiv.org/html/2602.11988v1)(개발자 작성 파일 +4%, 비용 +20%) | §9.1 기준선 5번째. 엔진이 "파일 하나"보다 나은지 직접 비교. 자가 검증(§9.2)에 "rules_file의 truth_change M1이 oracle보다 크다" 조건 추가 가능 |
| 2 | **데이터 파이프라인 선검증 + SWE-rebench-V2 조인**: ① `instance_id` 모델 간 중복 비율 통계, ② `created_at`으로 실제 시각 부여, ③ `FAIL_TO_PASS`와 `test_cmd`로 `signal: test` 이벤트 본문 생성, ④ 독립 출처는 "다른 모델 계열 + 다른 하네스"로 정의, ⑤ `resolved=-1` 제외. | M | [Open-SWE-Traces 카드](https://huggingface.co/datasets/nvidia/Open-SWE-Traces/raw/main/README.md)(시각/CI 필드 없음), [SWE-rebench-V2-PRs 카드](https://huggingface.co/datasets/nebius/SWE-rebench-V2-PRs/raw/main/README.md)(`created_at`, F2P) | §11 1·4단계. 가정이 깨지면 생성 전체가 막히므로 가장 먼저 검증할 위험 |
| 3 | **이력 길이/잡음 스윕 + "첫 결과 추종" 에이전트 변형**: 같은 시나리오에 무관한 Open-SWE-Traces 사실을 N배 채워(예: x1, x5, x25) M1·M2 곡선을 낸다. 별도로 "목록 1위를 무조건 따르는" 단순 에이전트를 민감도 분석용으로 둔다(판정에는 안 씀). | M | [Supersede](https://arxiv.org/abs/2606.27472)(24배 길이에 정확도 68%에서 28%), [Ground Truth First](https://arxiv.org/abs/2607.21962)(기간에 따른 순위 역전), [VibeMemBench](https://arxiv.org/html/2609.23570v1)(무관 대조군 -0.7~-3.4%p) | §7.1의 공백(무관 결과 피해 미측정) 보완. 관찰 지표로만 보고하면 §15 9번(사후 지표) 규칙과 충돌하지 않음. 사전 등록 전에 넣을 것 |
| 4 | **새 계열 `implicit_invalidation`**: 규칙이 코드 변경(커밋 이벤트)으로 암묵적으로 무효가 된다. 사람이 새 사실을 말하지 않는다. 사실에 `anchor`(파일 경로 등) 필드를 두고 `commit` 종류 이벤트가 앵커를 바꾼다. 부산물로 "f_new에서 파생된 간접 질문" 문항(MQuAKE식 다중 홉)을 `promote`에 추가. | M | [Copilot 메모리](https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/)(인용을 사용 시점에 검증), [MemStrata 후속](https://arxiv.org/abs/2608.20685)(실제 이력에서 이름 변경/이동/버전 변경 전이 130개) | §5.3 계열 추가. 생성은 "깨끗한 원자 전이" 유형(이름 변경, 엔드포인트 이동, 버전 올림)으로 제한 |
| 5 | **`leak_cross_repo`(같은 팀 안 저장소 간 누출)와 `conditional`의 저장소 조건 시나리오**: 저장소 A의 빌드/테스트 규칙이 저장소 B 질문에 나오면 오류(누출이 아니라 `block`). | S | [Copilot 문서](https://docs.github.com/en/copilot/concepts/agents/copilot-memory)(저장소 단위 범위), [Claude Code](https://code.claude.com/docs/en/memory)(자동 메모리는 저장소 단위) | 문항 `repo`와 `conditions: {repo: ...}`는 이미 지원(§5.2). 새 코드 없이 시나리오만 추가하면 됨 |
| 6 | **종단 검증 소집합(SWE 과업 10~20개)**: 소수의 실행 가능한 SWE 과업에 엔진이 만든 기억을 주입해 실제 해결률과 M1·M2 순위가 일치하는지 확인한다. 약한/강한 모델 2종으로 부호 반전도 점검. | L | [VibeMemBench](https://arxiv.org/abs/2609.23570)(검색 성공이 과업 성공이 아님: 11/12 조합이 기준선 이하), 같은 논문 본문(같은 기록이 모델에 따라 반대 효과) | M1·M2의 외적 타당성 점검. 비용과 LLM 비결정성이 커서 발표(10/29) 이후 과제로 권장 |

### 5.1 다음에 쓸 만한 보조 아이디어 (순위 밖)

- **`human_confirm` 문구 소스로 SWE-chat 활용**: 실제 사용자의 정정/반박 문구를 시나리오 말투 참고로 쓴다. ODC-By 라이선스와 접근 제한(gated)이 있으므로 [HF API](https://huggingface.co/api/datasets/SALT-NLP/SWE-chat) 기준으로 약관 확인 후 사용(M). 사용자가 반박하는 비율 50%라는 보고([초록](https://arxiv.org/abs/2604.20779))는 "같은 정정이 반복되면 규칙으로 승격되는가" 시나리오의 근거.
- **결과 본문의 "사용 가능 형태" 점검**: VibeMemBench에서 실패의 69.3%가 본문 형태 저하였다. 엔진이 돌려준 본문이 `markers`를 포함하더라도 원문 전사 덩어리인지 구분하는 관찰 지표(결과 길이 대비 `markers` 밀도)가 후보다(S, 관찰용).

---

## 6. 한계와 확인하지 못한 것

| 항목 | 상태 |
|---|---|
| 2026년 논문 다수(DreamBench-SWE, MemStrata 2종, Supersede, Ground Truth First, Procedural Memory, 두 에이전트 ablation)는 단일 저자이고 동료 심사 확인 못 함 | 수치는 자기 보고로만 인용 |
| Zep/Graphiti, Copilot 메모리 수치는 벤더 자기 보고 | 독립 재현 없음. 우리 하네스가 독립 검증 수단이 될 수 있음 |
| SkillsBench의 자가 생성 스킬 대 큐레이션 스킬, 소프트웨어 영역별 이득 | 초록에 없어 인용 안 함. 검색 스니펫에 다른 수치가 있었으나 판본 불일치로 미사용 |
| "이중 시간 엣지가 산문 인라인 이력보다 낮다(72/81 대 79/81)"는 검색 스니펫 | Ground Truth First 초록에서 확인 안 됨. **미사용** |
| Open-SWE-Traces의 `instance_id` 모델 간 중복 비율 | HF 필터 API 오류로 직접 확인 실패(개선안 2 ① 필요) |
| Cursor Memories 기능 제거 여부 | 공식 문서에서는 규칙 페이지에 언급이 없고 옛 주소가 리디렉션된다는 것까지만 확인 |
| SWE-Hero/Zero 카드 본문, SWE-chat의 라벨 구조, Zenodo CI 데이터 라이선스 | 열어 보지 못함 |
| SWE-ContextBench는 버전마다 규모가 다름(v2 요약 300+99, v3 1,100+376) | 최신 v3 초록 기준으로 적음 |
| 지식 편집(MQuAKE) 지표 정의 | 검색 스니펫 기반(낮음~중간) |
| 이번 검색 범위 밖: 상용 에이전트 내부의 메모리 평가, 비공개 벤치마크 | 부재를 주장하지 않음 |
