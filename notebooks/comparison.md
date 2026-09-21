# Strategy Performance Comparison: Strategy 1 vs Strategy 2

이 문서는 `notebooks/strategy_1.ipynb`와 `notebooks/strategy_2.ipynb`의 Train(In-Sample) 및 Test(Out-of-Sample) 구간 백테스트 성과 지표(Sharpe Ratio, MDD, MDD Duration, Return)를 비교 정리한 리포트입니다.

---

## 1. 성과 지표 종합 비교표

| 전략 | 구분 (구간) | Return [%] | Sharpe Ratio | Max Drawdown [%] | Max Drawdown Duration |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Strategy 1**<br />(다차원 휴리스틱 앙상블) | **Train** (In-Sample, 2022.10 ~ 2025.09) | **+31.59%** | **1.22** | **-6.66%** | **357일** |
| | **Test** (Out-of-Sample, 2025.10 ~ 2026.08) | **+12.18%** | **1.72** | **-0.86%** | **214일** |
| **Strategy 2**<br />(동적 롤링 선형회귀) | **Train** (In-Sample, 2023.10 ~ 2025.09)* | **+16.20%** | **1.03** | **-9.76%** | **304일** |
| | **Test** (Out-of-Sample, 2025.10 ~ 2026.08) | **+12.68%** | **2.83** | **-0.06%** | **4일** |

> *\* Strategy 2의 Train 구간 시작일(2023-10-24)은 126영업일 롤링 윈도우의 초기 적합(burn-in)으로 인해 Strategy 1(2022-10-04) 대비 짧습니다.*

---

## 2. 전략별 상세 백테스트 결과

### 1) Strategy 1 (`notebooks/strategy_1.ipynb`)
- **전략명**: `GoldEnsembleStrategy` (다차원 휴리스틱 앙상블 전략)
- **Train (In-Sample: 2022-10-04 ~ 2025-09-30, 1092일)**
  - **Return**: `31.58871%`
  - **Sharpe Ratio**: `1.21809`
  - **Max. Drawdown**: `-6.65586%`
  - **Max. Drawdown Duration**: `357 days`
  - **Avg. Drawdown Duration**: `87 days`
  - **# Trades / Win Rate**: 31회 / 51.61%
  - **Profit Factor**: `2.86369`
- **Test (Out-of-Sample: 2025-10-01 ~ 2026-08-31, 334일)**
  - **Return**: `12.17859%`
  - **Sharpe Ratio**: `1.72109`
  - **Max. Drawdown**: `-0.86286%`
  - **Max. Drawdown Duration**: `214 days`
  - **Avg. Drawdown Duration**: `214 days`
  - **# Trades / Win Rate**: 3회 / 66.67%
  - **Profit Factor**: `15.35599`

---

### 2) Strategy 2 (`notebooks/strategy_2.ipynb`)
- **전략명**: `RollingEconometricGoldStrategy` (동적 롤링 계량경제 회귀 전략)
- **Train (In-Sample: 2023-10-24 ~ 2025-09-30, 707일)**
  - **Return**: `16.19512%`
  - **Sharpe Ratio**: `1.02984`
  - **Max. Drawdown**: `-9.76487%`
  - **Max. Drawdown Duration**: `304 days`
  - **Avg. Drawdown Duration**: `75 days`
  - **# Trades / Win Rate**: 23회 / 47.83%
  - **Profit Factor**: `2.18699`
- **Test (Out-of-Sample: 2025-10-01 ~ 2026-08-31, 334일)**
  - **Return**: `12.68214%`
  - **Sharpe Ratio**: `2.83313`
  - **Max. Drawdown**: `-0.05543%`
  - **Max. Drawdown Duration**: `4 days`
  - **Avg. Drawdown Duration**: `4 days`
  - **# Trades / Win Rate**: 4회 / 100.0%
  - **Profit Factor**: `∞ (Zero Loss Trades)`

---

## 3. 벤치마크 대비 성과 요약 (Out-of-Sample: 2025.10 ~ 2026.08)

| 자산 / 전략 | Return [%] | Sharpe Ratio | Max Drawdown [%] |
| :--- | :---: | :---: | :---: |
| **Strategy 2 (Rolling Econometric)** | **+12.68%** | **2.83** | **-0.06%** |
| **Strategy 1 (Heuristic Ensemble)** | **+12.18%** | **1.72** | **-0.86%** |
| KODEX S&P500 | +13.86% | 1.16 | -9.35% |
| KODEX Money Market (KOFR) | +2.44% | 20.32 | -0.00% |
| Proxy Gold (`411060.KS`, Buy & Hold) | +1.62% | 0.23 | -32.09% |
| US Short Bond | +1.50% | 0.21 | -10.76% |

---

## 4. 핵심 분석 및 결론

1. **위험 대비 수익률(Sharpe Ratio) 대폭 향상**:
   - Out-of-Sample 구간에서 Strategy 2의 Sharpe Ratio는 **2.83**으로 Strategy 1(**1.72**) 대비 약 **64.7%** 향상되었습니다.
2. **하방 리스크(MDD) 방어 및 회복 기간 단축**:
   - 2025년 하반기~2026년 금 현물 시장의 큰 조정(Buy & Hold 기준 MDD **-32.09%**) 구간에서 Strategy 2는 불리한 매크로 국면 시 전액 현금(KOFR 금리) 보유를 통해 MDD를 **-0.06%**로 억제했습니다.
   - 최대 드로다운 지속 기간(MDD Duration) 역시 Strategy 1의 **214일**에서 Strategy 2는 **4일**로 비약적으로 단축되었습니다.
3. **정적 가중치(Strategy 1) vs 동적 민감도 추정(Strategy 2)**:
   - Strategy 1은 매크로 지표에 고정 가중치(1/3씩)를 부여한 반면, Strategy 2는 126일 롤링 Ridge 회귀를 통해 원/달러 환율($\beta_{FX}$), 미국채 10년물 금리($\beta_{TNX}$), 달러 인덱스($\beta_{DXY}$)의 국면별 민감도 변화를 반영하여 보다 안정적인 진입/청산 시그널을 생성했습니다.
