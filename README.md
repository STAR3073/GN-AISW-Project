# DocAction AI

문서 업로드 → Action 추출 → 역산 계획 → Tool 실행 → 상태 추적.

경진대회 8일 MVP. 로그인/실 Calendar/RAG는 포함하지 않는다.

## 실행

```powershell
cd "d:\langchain-kr\260929_GN AI Project\docaction-ai"
pyenv exec python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# .env 에 OPENAI_API_KEY 입력
python -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

브라우저: http://127.0.0.1:8000

이 환경에 Node가 없어 Next.js 대신 **같은 3열 Dashboard를 FastAPI가 서빙**한다.

## Demo 스크립트

1. 공고문 PDF 업로드
2. `이 문서 확인해서 우리가 해야 할 일 정리하고 진행해줘.`
3. 우측 Action / 하단 Timeline·Activity Log 확인
4. `아직 해야 할 일이 뭐야?`
5. `완료보고서 초안 만들어줘.`
