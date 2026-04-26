# Morning Crypto Agent

매일 오전 6시(KST) 암호화폐 5종(BTC/ETH/DOGE/SOL/XRP)의 시장 데이터를 수집하고 Discord로 분석 리포트를 전송하는 자동화 루틴.

## 구조

```
.github/workflows/morning.yml   # GitHub Actions: 데이터 수집 + data.json 커밋
collect_data.py                 # Binance/yfinance에서 지표 계산 후 data.json 생성
requirements_morning.txt        # Python 의존성
data.json                       # 수집 결과 (Actions가 main 브랜치에 자동 커밋)
```

## 워크플로우 실행 방식

`morning.yml`은 두 가지 트리거를 지원한다:
- **workflow_dispatch**: 클라우드 루틴이 매일 06:00 KST에 API로 수동 트리거
- **cron (21:25 UTC / 06:25 KST)**: 루틴이 실패할 경우를 대비한 자동 폴백

루틴은 자신이 dispatch한 run의 `run_id`를 추적해 완료를 기다린 뒤 `data.json`을 읽는다.

## data.json 스키마

```json
{
  "timestamp": "YYYY-MM-DD HH:MM UTC",
  "coins": {
    "<SYMBOL>": {
      "price": float,
      "rsi": float,          // 14-period RSI (0-100)
      "macd": "+" | "-",
      "bb": "아래이탈" | "하단" | "중간" | "상단" | "위이탈",
      "prob_1d": float,      // 1일 뒤 상승 확률 (%)
      "prob_7d": float,      // 7일 뒤 상승 확률 (%)
      "prob_30d": float,     // 30일 뒤 상승 확률 (%)
      "similar_n": int,      // 유사 패턴 발견 횟수 (낮을수록 신뢰도 낮음)
      "key_level": float,    // 가장 가까운 주요 지지·저항 레벨
      "level_dist": float,   // 현재가 → key_level 거리 (%)
      "top_combo_rate": float // 최상위 지표 조합 성공률 (%)
    }
  },
  "macro": {
    "fg": int,               // 공포탐욕지수 (0-100)
    "dxy": "up" | "down" | "unknown",
    "nasdaq": "up" | "down" | "unknown"
  }
}
```

raw URL (루틴에서 사용):
```
https://raw.githubusercontent.com/shin9602/morning-crypto-agent/main/data.json
```

## 환경변수 (클라우드 루틴에 설정)

| 변수 | 용도 |
|------|------|
| `GITHUB_TOKEN` | Actions 워크플로우 dispatch 및 run 상태 조회 |
| `DISCORD_WEBHOOK_URL` | 분석 결과 전송 |

## 비용 구조 (5× 레버리지 기준)

| 전략 | 기간 | 총비용 |
|------|------|--------|
| 단타 | 1일  | 0.55%  |
| 스윙 | 7일  | 1.45%  |
| 장기 | 30일 | 4.90%  |

## 클라우드 루틴 설정

- **일정**: 매일 06:00 (Asia/Seoul)
- **저장소 접근**: `shin9602/morning-crypto-agent`
- **네트워크 접근**: 활성화
- **Timezone**: `Asia/Seoul`
