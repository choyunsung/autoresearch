# AutoResearch Collab — Collaborative Research Platform

autoresearch 기반 협업 연구 플랫폼. 여러 연구자가 실험 결과를 공유하고, 피드백을 교환하며, 다른 사람의 연구를 포크해서 발전시킨 후 머지할 수 있습니다.

## Quick Start

```bash
# 1. 의존성 설치
pip install -r requirements-platform.txt

# 2. 서버 실행 (기본 포트: 7891)
./run.sh

# 또는 직접 실행
PYENV_VERSION=3.11.13 python -m uvicorn collab.app:app --reload --port 7891
```

**http://localhost:7891** 에서 접속

## Architecture

```
autoresearch-collab/
├── train.py                  # 기존 autoresearch 훈련 스크립트
├── prepare.py                # 데이터 준비 (read-only)
├── program.md                # 에이전트 프롬프트 (Research Config에서 자동 생성)
├── sync_results.py           # CLI 동기화 도구
├── run.sh                    # 서버 실행 스크립트
├── requirements-platform.txt # 플랫폼 의존성
│
├── collab/                   # 웹 플랫폼 패키지
│   ├── app.py                # FastAPI 메인 앱 + 라우팅
│   ├── models.py             # SQLAlchemy 모델 (8개 테이블)
│   ├── database.py           # SQLite 연결
│   ├── auth.py               # PBKDF2 + HMAC 토큰 인증
│   ├── research_config.json  # 연구 페르소나/목적 설정
│   ├── linked_projects.json  # 자동 동기화 대상 프로젝트
│   │
│   ├── api/                  # REST API
│   │   ├── experiments.py    # 실험 CRUD + 배치 동기화
│   │   ├── threads.py        # 연구 스레드 API
│   │   ├── research_config.py # 연구 설정 + program.md 생성
│   │   └── config_sync.py    # 연결 프로젝트 동기화
│   │
│   ├── web/                  # 웹 라우트 (분리된 모듈)
│   │   ├── forks.py          # 포크/머지 리퀘스트
│   │   └── profile.py        # 프로필/API 토큰
│   │
│   ├── templates/            # Jinja2 HTML (18개)
│   └── static/style.css      # 다크 테마 CSS
```

## Database Models

| 모델 | 역할 |
|------|------|
| `Researcher` | 연구자 (username, password, institution, GPU info) |
| `Experiment` | 실험 결과 (val_bpb, metrics, code diff, hyperparams) |
| `ExperimentTag` | 실험 태그 (many-to-many) |
| `ExperimentComment` | 실험 피드백/댓글 |
| `ResearchThread` | 연구 스레드 (hypothesis/discussion/insight/question) |
| `ThreadComment` | 스레드 댓글 (실험 결과 링크 가능) |
| `ResearchFork` | 연구 포크 (다른 연구자의 실험을 기반으로 분기) |
| `MergeRequest` | 머지 리퀘스트 (포크 결과를 원본 연구에 합류) |
| `MergeRequestComment` | 머지 리퀘스트 토론 |

## Features

### 1. Authentication
- 회원가입/로그인 (PBKDF2-SHA256 비밀번호 해싱)
- 72시간 HMAC 토큰 (쿠키 기반 웹 + Bearer 기반 API)
- API 토큰 생성 (CLI 도구용, Profile > API Token)
- SECRET_KEY는 `.secret_key` 파일로 서버 재시작 후에도 유지

### 2. Experiments
- 실험 결과 CRUD (val_bpb, memory, MFU, params, depth 등)
- 코드 diff 및 하이퍼파라미터 스냅샷 저장
- 연구자/브랜치/상태 필터링
- 실험별 피드백 댓글

### 3. Research Fork & Merge
- **Fork**: 다른 연구자의 실험(keep 상태)에서 Fork 생성
  - 포크 브랜치 이름 지정
  - 소스 커밋 해시 기록
- **독립 연구**: 포크 브랜치에서 자체 실험 수행 후 sync
- **Merge Request**: 개선 결과를 원본 연구자에게 제출
  - 자동 메트릭 비교 (val_bpb 향상량)
  - 원본 연구자만 Approve/Reject/Merge 가능
  - 토론 댓글

### 4. Research Threads
- 가설(hypothesis), 토론(discussion), 인사이트(insight), 질문(question)
- 실험 결과를 증거로 링크 가능
- 핀 고정

### 5. Leaderboard
- 연구자별 최고 val_bpb 랭킹
- GPU 정보, 실험 수 표시

### 6. Research Configuration
- **페르소나**: AI 에이전트의 연구 역할 정의
- **목적**: 연구 목표 및 핵심 지표
- **방법론**: 연구 접근법
- **제약/평가 기준**: 추가 조건
- **자동 program.md 생성**: 설정 변경 시 에이전트 프롬프트 자동 재생성
- **Linked Projects**: 설정 변경이 연결된 프로젝트(예: amcgx-test)에 자동 배포

### 7. Dashboard
- 실시간 통계 (연구자 수, 실험 수, 최고 val_bpb)
- val_bpb 진행 차트 (Chart.js)
- 최근 실험/스레드 피드
- 리더보드 미니뷰

## CLI Sync Tool

로컬에서 실행한 autoresearch 실험 결과를 플랫폼에 업로드합니다.

```bash
# 1. 로그인 (토큰 저장)
python sync_results.py login --username myuser --password mypass --server http://localhost:7891

# 2. results.tsv 일괄 동기화
python sync_results.py sync --branch autoresearch/mar16 --with-diffs

# 3. 단일 실험 보고 (program.md 루프에서 자동 호출)
python sync_results.py report \
  --branch autoresearch/mar16 \
  --commit abc1234 \
  --val-bpb 0.985 \
  --memory-gb 44.0 \
  --status keep \
  --description "deeper model 12 layers" \
  --depth 12 \
  --with-diffs
```

## API Endpoints

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/token` | API 토큰 발급 (username + password) |

### Experiments
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/experiments` | 실험 목록 (필터: researcher_id, branch, status) |
| POST | `/api/experiments` | 실험 생성 (Bearer 토큰 필요) |
| POST | `/api/experiments/sync` | 배치 동기화 (results.tsv 일괄 업로드) |
| GET | `/api/experiments/{id}` | 실험 상세 |
| GET | `/api/experiments/leaderboard/best` | 리더보드 |

### Threads
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/threads` | 스레드 목록 |
| POST | `/api/threads` | 스레드 생성 |
| GET | `/api/threads/{id}` | 스레드 상세 |

### Research Config
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/research-config` | 현재 설정 |
| PUT | `/api/research-config` | 설정 업데이트 |
| GET | `/api/research-config/generate-prompt` | program.md 생성 |

### Config Sync
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/config-sync/projects` | 연결된 프로젝트 목록 |
| POST | `/api/config-sync/projects` | 프로젝트 연결 |
| POST | `/api/config-sync/push` | 설정 일괄 배포 |

## Workflow: 연구 협업 시나리오

### 기본 흐름
```
1. 연구자 A: Register → autoresearch 실행 → sync_results.py 동기화
2. 연구자 B: Dashboard에서 A의 결과 확인 → Experiment에 피드백
3. 연구자 B: A의 best experiment에서 Fork
4. 연구자 B: Fork 브랜치에서 자체 autoresearch 실행
5. 연구자 B: Merge Request 생성 (개선 결과 제출)
6. 연구자 A: MR 검토 → Approve/Merge
7. Research Thread에서 발견 공유 + 토론
```

### program.md 동기화 흐름
```
Research Config 페이지에서 설정 변경
    ↓
program.md 자동 재생성
    ↓
Linked Projects에 자동 배포 (예: amcgx-test/program.md)
    ↓
모든 연구 에이전트가 동일한 페르소나/목적으로 연구 수행
```

## Configuration Files

### research_config.json
연구 페르소나, 목적, 방법론 등. 웹 UI에서 편집 가능.

### linked_projects.json
```json
[
  {
    "name": "amcgx-test (MCG Research)",
    "path": "/Users/yunsung/workspace/amcgx-test",
    "sync_program_md": true,
    "sync_config_json": true
  }
]
```

### .secret_key
서버 인증 토큰 서명용. 자동 생성됨. `.gitignore`에 추가할 것.

## Development

```bash
# DB 리셋
rm collab/research.db

# 서버 실행 (auto-reload)
PYENV_VERSION=3.11.13 python -m uvicorn collab.app:app --reload --port 7891

# 포트 변경
PORT=9999 ./run.sh
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI 0.115+ |
| Database | SQLite (SQLAlchemy ORM) |
| Frontend | Jinja2 + vanilla JS + Chart.js |
| Auth | PBKDF2-SHA256 + HMAC token |
| Styling | Custom dark CSS |
