---
name: superpowers-sdd
description: >-
  Use this skill when designing or implementing features, architectures, or substantial changes
  in this project following the Superpowers Spec-Driven Development (SDD) methodology.
---

# Superpowers: Spec-Driven Development (SDD) Workflow

이 스킬은 Superpowers 프레임워크의 핵심인 **스펙 주도 개발(SDD)** 및 **엄격한 TDD** 워크플로우를 안내합니다.

---

## 1. 워크플로우 개요

```text
[요구사항 분석] ──> [스펙(Spec) 문서 작성] ──> [실행 계획(Plan) 수립] ──> [사용자 승인] ──> [TDD 구현] ──> [검증]
```

1. **Spec 문서 작성**: `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md`
2. **Plan 문서 작성**: `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
3. **사용자 승인**: 계획 검토 요청 후 사용자의 승인을 받음
4. **TDD 사이클**:
   - 단위 테스트 파일(`tests/test_<module>.py`)에 실패하는 테스트 추가
   - 구현 코드 작성
   - 테스트 통과 확인 (`pytest tests/test_<module>.py -v`)
5. **전체 회귀 검증**: `pytest -v`

---

## 2. 스펙 문서 템플릿 (`docs/superpowers/specs/`)

```markdown
# [기능명] 설계

작성일: YYYY-MM-DD
목표: [기능의 목적 및 배경 한 줄 요약]

## 1. 요구사항 및 배경
- 사용자의 핵심 요구사항
- 해결하려는 문제점

## 2. 시스템 아키텍처 및 데이터 흐름
- 모듈 간의 상호작용
- 입력 및 출력 데이터 구조

## 3. DB 스키마 및 API 변경
- 테이블 변경 또는 신규 API 명세

## 4. 엣지 케이스 및 위험 관리
- 네트워크 지연, API 오류, 데이터 누락 시 대응책
```

---

## 3. 실행 계획 템플릿 (`docs/superpowers/plans/`)

```markdown
# [기능명] 구현 계획

스펙 문서: `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md`

## 태스크 목록

- [ ] Task 1: 단위 테스트 작성 (Red)
- [ ] Task 2: 핵심 모듈 구현 (Green)
- [ ] Task 3: 통합 및 리팩토링 (Refactor)
- [ ] Task 4: 회귀 테스트 및 운영 검증
```

---

## 4. TDD 실행 규칙

- 테스트 파일 위치: `trader/tests/`
- 테스트 실행 명령:
  ```powershell
  .\.venv\Scripts\python -m pytest tests\test_<target>.py -v
  ```
- 원칙:
  1. 구현 코드를 건드리기 전에 반드시 실패하는 테스트를 먼저 작성합니다.
  2. 한 번에 하나의 테스트만 통과시키는 최소 단위 구현을 지향합니다.
  3. 모든 테스트 통과 후 리팩토링합니다.
