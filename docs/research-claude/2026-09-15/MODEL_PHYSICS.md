# 채택 모델의 물리식 명세 — 경량 2-D plane-pair 하이브리드 (2026-09-15)

대상 독자는 SI/PI 엔지니어(소유자)다. 식은 코드에 **실제로 구현된 형태**로 적었다. 교과서 형태와 다른 곳은 **[구현 차이]**로 표시했다. 코드 위치 약칭은 다음과 같다.

- `M1` = `tools/research-claude/exp1/model.py`, `1b` = `exp1/exp1b.py`, `RE1` = `exp1/run_exp1.py`
- `M3` = `exp3/model3.py`, `HG` = `exp3/homog.py`, `R3` = `exp3/run3.py`
- `R4` = `exp4/run4.py`, `P5` = `exp5/pipeline.py`, `K7` = `exp7/kernel.py`, `R8` = `exp8/run8.py`
- `MF` = `src/spd_decap_pi/_core/solver/mfdm.py`, `VM` = `src/spd_decap_pi/_core/via_model.py`
- `SP` = `src/spd_decap_pi/_core/models/spice.py`, `TS` = `src/spd_decap_pi/_core/io/touchstone.py`

`solver/via_model.py`라는 경로는 없고, 실제 파일은 `_core/via_model.py`다. 채택 모델은 EXP-5 동결형(S2 + EXP-4 (b))에 EXP-8 기준면 규칙을 더한 것이다. 클래스 사슬은 `R8.run` → `P5.ModelB` → `R4.Model4` → `M3.Model3`이다.

---

## 1. 문제 정의와 참조

**S→Z 변환.** 참조는 PowerSI Touchstone(`# Hz S RI R 1`, 92포트)이다. 포트 전체 행렬을 한 번에 변환한다.

$$\mathbf Z = Z_0(\mathbf I+\mathbf S)(\mathbf I-\mathbf S)^{-1},\qquad Z_0 = 1\,\Omega$$

- 구현: `TS:289`는 `Z0·solve((I−S)ᵀ,(I+S)ᵀ)ᵀ`로 역행렬 없이 푼다. 조건수 가드는 $\mathrm{cond}(\mathbf I-\mathbf S)\,\epsilon_{\text{mach}}\le10^{-8}$(`TS:285-287`)이다. 잔차 가드는 $\lVert \mathbf Z\mathbf Z_0^{-1}(\mathbf I-\mathbf S)-(\mathbf I+\mathbf S)\rVert/\lVert\mathbf I+\mathbf S\rVert\le10^{-10}$(`TS:292-294`)이다. N≥3 포트 데이터는 행 우선 순서로 읽는다(`TS:265-266`).
- 비교량은 $Z_{pp}$(다른 포트 개방)다(`TS:301-311`).
- 주의: 연구에 쓴 `S4LB002_*_Zdiag.npz`는 사용자 PC 분석 스크립트로 만들었다. 식은 같지만 `TS`로 생성했다는 기록은 없다.
- 출처: [1] Pozar §4.3(S↔Z 관계), [2] Touchstone 2.0 사양.

**오차 지표.** 복소 상대오차는 다음과 같다(`RE1:42`의 `rel`, `R3:45-47`, `P5:122`).

$$e = \frac{|Z_m-Z_r|}{|Z_r|}$$

$|Z_m|=|Z_r|$이고 위상차가 $\Delta\varphi$이면 다음이 성립한다.

$$e=\left|e^{j\Delta\varphi}-1\right|=\sqrt{2-2\cos\Delta\varphi}=2\left|\sin\tfrac{\Delta\varphi}{2}\right|$$

따라서 크기가 같아도 위상차 5.7°는 곧 10% 오차다. 공진 부근처럼 $X\approx0$인 대역에서 L 오차가 위상으로 증폭되는 이유다.

**게이트 G1–G5** (`RE1:58-83`, 사다리형 `R3:36-54`)

| 게이트 | 대역 | 조건 |
|---|---|---|
| G1 | 1–100 kHz | 모든 점 $\lvert\mathrm{Re}\,Z_m-\mathrm{Re}\,Z_r\rvert\le0.05$ mΩ |
| G2 | 0.1–1 MHz | $\Delta L(f)=(\mathrm{Im}Z_m-\mathrm{Im}Z_r)/\omega\in[-5,+5]$ pH |
| G3 | 1 MHz | $e<10\%$ |
| G4 | 1–10 MHz | 모든 점 $e<20\%$ 이고 $\lvert f_{res,m}-f_{res,r}\rvert/f_{res,r}<10\%$ |
| G5 | 10–100 MHz | 보고만 |

- $f_{res}$는 $\min|Z|$ 점에 $(\ln f,\ln|Z|)$ 포물선 보간을 적용해 구한다(`RE1:46-55`).
- `R3:49`의 사다리 판정은 참조 공진을 port18 값 1.585 MHz로 **고정**한다.

---

## 2. 2-D transmission-plane / M-FDM 단위셀

레일 평면을 층마다 정사각 격자(셀 크기 $h$)로 나눈다. 셀 중심이 절점이며, 4-이웃 edge와 절점-기준 shunt로 절점 어드미턴스 행렬을 만든다(`M3:465-499`). 절점 $i$의 KCL은 다음과 같다.

$$\sum_{j\in\mathcal N(i)}\frac{V_i-V_j}{Z_{ij}(\omega)} + Y_{sh,i}(\omega)\,V_i + \sum_{k\in\text{decap}(i)}Y_{dec,k}(\omega)\,V_i + \sum_{\text{via/trace}}\frac{V_i-V_m}{Z_{im}} = I_i$$

기준(이상 GND)은 행렬에서 제거한 0 V 절점이다. 주파수마다 `splu`로 직접 해석한다(`M3:505-508`).

**Edge 임피던스.** 구현은 `M3:449-463`을 `R4:75-88`이 대체한다.

$$Z_{ij} = Z_s(f)\,\frac{\ell}{w\,G} + j\omega\mu_0\,d_{e}\,\frac{\ell}{w_{\text{eff}}},\qquad w_{\text{eff}} = G\,w\ (\text{fringing은 §4})$$

- 내부 edge는 $\ell=w=h$이므로 1 square다. 정사각 셀에서 edge 임피던스는 per-square 값과 같다(차원: Ω/□ × □).
- fine–coarse 경계 edge는 $\ell=(h_c+h_f)/2$, $w=h_f$다(`M3:115`, `M3:102`).
- $G$는 균질화한 상대 전도도다(§5, 완전 시트 = 1).
- $d_e$는 양 끝 셀 $d_{\text{eff}}$의 **산술평균**이다(`M3:257`).

**왜 $\mu_0 d$ per square인가.** 간격 $d$, 폭 $w$, 길이 $\ell$인 평행판에서 $w\gg d$이면 TEM 자계는 $H=I/w$로 판 사이에 갇힌다. 저장 에너지는 $\tfrac12\mu_0H^2(w\,d\,\ell)=\tfrac12 L I^2$이므로 다음이 된다.

$$L=\mu_0 d\,\ell/w$$

즉 per square로 $\mu_0 d$다(d = 100 µm → 125.7 pH/□). M-FDM 단위셀 $L=\mu d$, $C=\varepsilon h^2/d$와 같다.
- 출처: [3] Swaminathan & Engin 2007(transmission-plane/M-FDM 장), [4] Engin–Bharath–Swaminathan 2007, [5] Kim & Swaminathan 2002(TMM 단위셀), [6] Bharath–Choi–Swaminathan ECTC 2009.
- 제품 커널 `MF`는 같은 단위셀을 다도체로 일반화했다. C 스탬프는 `MF:891-901`, $G=\omega C\tan\delta$는 `MF:1076-1077`에 있다. 채택 모델이 `MF`에서 가져다 쓰는 것은 표면임피던스 함수뿐이다.

**Shunt 어드미턴스.** 구현은 `1b:125-143`, `M3:479`다.

$$Y_{sh}= (\omega C\tan\delta + j\omega C) = j\omega C(1-j\tan\delta),\qquad C = A_{cell}\sum_{\text{sides}}\frac{\varepsilon_0}{\sum_k t_k/\varepsilon_{r,k}}$$

- $A_{cell}=h^2\cdot\text{fill}$이다(금속 채움률 가중, `M3:105`).
- 유전체가 여러 층이면 직렬 합성한다: $\varepsilon_0/\sum t_k/\varepsilon_{r,k}$.
- **[구현 차이]** 채택 경로에서는 $\varepsilon_r$을 행의 `dk` 값(없으면 1 MHz 보간값)으로, $\tan\delta$를 첫 유전체의 1 MHz 값으로 **고정**한다(`1b:131-134`). 주파수 테이블 보간(`M1:105-111`)은 비채택 변형 A/B에서만 쓴다.
- **[구현 차이 — 오류, 2026-09-16 발견]** 채택 경로의 셀 C는 `EPS0 * 1e-12 / Σ(t_um/εr)`(`1b:131-133`)로 간격의 µm→m 환산 1e-6이 빠져 **1e6배 작다**(P18 총 평면 C 4.7e-6 nF, 올바른 값 약 4.7 nF). 변형 A/B의 `M1:525`는 올바르다. 수정은 플래그 `c_unit_fix`(EXP-13)로만 켠다. 이 오류로 EXP-11의 (a)(b) 시험은 무효였다.
- **[구현 차이]** C는 **바로 인접한 도체가 벽일 때만** 둔다(`1b:129`, `k == 0`). GND-only 모드에서 인접층이 타넷 평면이면 그쪽 C는 생략된다. cavity-wall 모드에서는 인접 타넷 금속과의 C를 이상 기준으로 연결한다.

**양측 기준 유도(2줄).**
- 위/아래 귀환 루프는 같은 전압강하를 받는 병렬 인덕터 $\mu_0d_{up}$, $\mu_0d_{dn}$이다. 따라서 $L=\big(\tfrac1{\mu_0 d_{up}}+\tfrac1{\mu_0 d_{dn}}\big)^{-1}=\mu_0\frac{d_{up}d_{dn}}{d_{up}+d_{dn}}\equiv\mu_0 d_{\text{eff}}$다(`1b:140`).
- 레일에서 두 벽까지의 캐패시턴스는 병렬이므로 $C=\varepsilon A(1/d_{up}+1/d_{dn})$다(`1b:143`).
- 가정: 두 벽은 등전위(이상 기준)이고, 두 cavity 사이 상호결합은 없다.
- 예: L14는 $d_{up}=30$, $d_{dn}=80$ µm → 21.8 µm이고, L25는 100/237 µm → 70.3 µm다.
- $d_{side}$는 두 도체 사이 **모든 행 두께의 합**이다(`1b:125`). 인접하지 않은 벽이면 중간 도체 두께도 포함한다.
- 양쪽 모두 못 찾으면 변형 B 규칙으로 대체한다: 인접 DGND 간격의 조화합, 또는 이름상 최근접 DGND(`1b:157-163`).

---

## 3. 도체 표면 임피던스와 내부 인덕턴스

$$\delta=\frac1{\sqrt{\pi f\mu_0\sigma}},\qquad \gamma=\frac{1+j}{\delta}=\sqrt{j\omega\mu_0\sigma},\qquad Z_c=\frac{1+j}{\sigma\delta}=\sqrt{\frac{j\omega\mu_0}{\sigma}}=\frac{\gamma}{\sigma}$$

`MF:755`는 $\gamma t$를, `MF:773`은 $Z_c$를 이 형태로 계산한다.

**한쪽 면(one-face) 유한두께 시트.** 구현은 `MF:735-798`의 `copper_surface_impedance`다.

$$Z_s^{(1)}=Z_c\coth(\gamma t)$$

$|\gamma t|<10^{-4}$이면 급수 $\coth x = 1/x + x/3 - x^3/45 + 2x^5/945$를 쓴다(`MF:760-766`). $f=0$이면 $1/(\sigma t)$다(`MF:776-777`).

저주파 극한은 $\gamma^2=j\omega\mu_0\sigma$를 쓰면 다음과 같다.

$$Z_c\Big(\frac1{\gamma t}+\frac{\gamma t}{3}\Big)=\frac{1}{\sigma t}+\frac{\gamma^2 t}{3\sigma}=\frac1{\sigma t}+j\omega\frac{\mu_0 t}{3}$$

즉 $R=1/(\sigma t)$, $L_{int}=\mu_0t/3$다.

**대칭 양면(two-face) 시트.** 구현은 `R4:47-57`의 `zs_two`다.

두 면 전류가 같을 때($I_1=I_2=I/2$), 2×2 면 행렬 $Z_c\begin{bmatrix}\coth\gamma t&\operatorname{csch}\gamma t\\ \operatorname{csch}\gamma t&\coth\gamma t\end{bmatrix}$(`MF:801-825`)에서 다음을 얻는다.

$$V = Z_c(\coth\gamma t+\operatorname{csch}\gamma t)\tfrac I2=\tfrac12 Z_c\coth\!\big(\tfrac{\gamma t}{2}\big)I$$

항등식 $\coth x+\operatorname{csch}x=\coth(x/2)$를 썼다. 저주파 극한은 다음과 같다.

$$\tfrac12Z_c\Big(\frac{2}{\gamma t}+\frac{\gamma t}{6}\Big)=\frac1{\sigma t}+j\omega\frac{\mu_0t}{12}$$

- 적용 규칙: 레일 평면 층의 coarse 셀 중 **과반이 양측**이면 그 층 전체에 $Z_s^{(2)}$를 쓰고, 나머지 층·trace에는 $Z_s^{(1)}$를 쓴다(`P5:42-62`).
- **[구현 차이]** 양측 여부는 셀 단위가 아니라 **층 단위 과반**으로 정한다.
- **[구현 차이]** 교과서 M-FDM 셀 R은 두 판을 모두 포함한다: $R=2/(\sigma t)+2\sqrt{j\omega\mu/\sigma}$ [4]. 채택 모델은 이상 GND이므로 **레일 시트의 $Z_s$만** 넣고 벽(귀환면)의 $Z_s$는 0이다(`M3:452-453`, `R4:77`). 연구 로그 §3의 "Zs_rail + Zs_wall" 표기는 비채택 변형 A(`M1:509-511`)에만 해당한다.
- 상수: $\sigma=5.959\times10^7$ S/m. SPD MetalModel에서 층 행 `conductivity`로 읽는다(`exp1/extract.py:85`). 같은 값이 `VM:10`, `K7:20`에 하드코딩돼 있다.
- 두께: 빌드업층 20 µm(L14), 코어층 32 µm(L25). $\delta=t$가 되는 주파수는 32 µm에서 약 4.2 MHz, 20 µm에서 약 10.6 MHz다(1 MHz에서 $\delta\approx65$ µm). 32 µm의 내부 L은 한쪽식 13.4 pH/□, 양측식 3.4 pH/□다.
- 출처: [7] Ramo–Whinnery–Van Duzer(유한두께 도체 표면임피던스), [8] Hall & Heck(내부 인덕턴스·표면 거칠기 이전 기본식), [9] Novak 2000(얇은 도체 평면쌍 손실).

---

## 4. 좁은 도체의 fringing 보정

**Trace.** 구현은 `M3:285-288`(= `M1:340-347`)이다.

$$Z_{tr}=Z_s^{(1)}\frac{\ell}{w}+j\omega\,\mu_0 d\,\frac{\ell}{w+2d}$$

- $d$는 이름상 최근접 DGND 층까지의 간격이다(중간 도체 두께 포함, `M1:88-102`).
- 폭이 없으면 층 기본값 25 µm, GND를 못 찾으면 $d=30$ µm를 쓴다.

**평면 셀.** 구현은 `R4:82-87`이다.

$$w_{\text{eff}}=\begin{cases}\min(Gw+2d_e,\ w) & Gw<5d_e\\ Gw & \text{otherwise}\end{cases}\qquad L_\square^{\text{eff}}=\mu_0 d\,\frac{w}{w+2d}$$

- 근거: 폭 $w$인 스트립 위 평행판 자계는 가장자리 밖으로 약 $d$씩 퍼진다. 그래서 유효폭이 $w+O(d)$가 된다. Wheeler의 넓은 스트립 근사 [10]가 이 보정의 원형이다. 다만 **정확히 $w+2d$라는 계수는 공학적 근사**이며 [10]의 식 그대로가 아니다 [미검증].
- **[구현 차이]** 상한 $\min(\cdot,w)$ 때문에 보정은 셀 폭을 넘지 못한다. 문턱 $5d$는 경험적 선택이다(`M3:205`, `fringe_wd=5.0`).

---

## 5. 균질화 (`HG`)

1. **래스터화**(`HG:19-48`): 셀을 sub×sub 서브타일로 나눈다. PowerSI 불리언 순서대로 양/음 도형을 칠하고, 같은 층 레일 trace와 패드를 금속으로 덧칠한다(`M3:239`). 서브타일 크기는 coarse 200/20 = 10 µm, fine 50/10 = 5 µm, TOP 50/10 = 5 µm다(`P5:108`).
2. **창 전도도**(`HG:117-138`): 이웃 두 셀의 중심-중심 구간(길이 $h$, 폭 $h$)을 창으로 잘라낸다. 창 안 금속 서브타일의 4-이웃 저항망에서 양 끝 면을 1 V/0 V로 두고 푼다(경계 half-bond 계수 2, `HG:63`). batched Jacobi-PCG를 쓴다(`HG:51-98`).
   $$G_{x}=I\cdot\frac{n_x}{n_y}\quad(\text{완전 시트}=1,\ \text{빈 창}=0)$$
   $y$ 방향은 창을 전치해 같은 방식으로 구한다. 따라서 셀마다 방향별 $G_x$, $G_y$가 생긴다. 이것이 대각 이방성 전도도 $\sigma_x=G_x\sigma$, $\sigma_y=G_y\sigma$다.
3. **사용**: $R$은 $1/G$로 늘린다. $L$도 $w_{\text{eff}}=Gw$를 통해 $1/G$로 늘린다(§2). $C$는 fill 가중이다.

**한계**
- 텐서의 비대각 성분이 없다. 창 폭이 한 셀이라 이웃 창 사이 가로 전류 재분배를 무시한다.
- 서브타일 4-연결이라 대각 접촉을 끊긴 것으로 본다. 5–10 µm 이하 형상은 계단화된다.
- **[구현 차이]** $L$에 $R$과 같은 $1/G$를 적용하는 것은 구멍 크기 $\gg d$일 때만 맞다. 구멍 $\lesssim d$이면 자계가 구멍을 메워 $L$ 증가가 작다.
- EXP-3에서 S1은 L14 R·L25 L을 거의 바꾸지 않았다(G3 16.3→15.6%).

---

## 6. Via 모델

**R.** 구현은 `VM:132-154`, 호출은 `M3:301-306`이다.

$$R_{via}=\frac{\ell}{\sigma A},\quad A=\begin{cases}\pi (D/2)^2 & \text{충전 microvia: COPPER, }D\le150\,\mu\text{m, 도체2+유전체1, }t_d/D\le1\\ \pi D\,t_p,\ t_p=\min(20\,\mu\text{m},D/4) & \text{도금 배럴(그 외)}\end{cases}$$

- $\ell$은 두 도체층 **중심 간 거리**다(`M3:301`).
- **[구현 차이]** 배럴 면적은 얇은 껍질 근사 $\pi D t_p$다. 정확한 고리 면적 $\pi(Dt_p-t_p^2)$보다 크며, $t_p=D/4$이면 33% 크다(`VM:79`).
- GND via의 R은 채택 모델에 넣지 않는다(이상 GND; `M3:308`, 1b에서는 `gnd_via_r_factor=0`).

**L.** 구현은 `M3:307-308`(= `M1:371-379`)이다.

$$L_{via}=\frac{\mu_0}{2\pi}\,\ell\,\ln\frac{s}{r},\qquad r=D/2,\quad s=\mathrm{clip}(\text{같은 윗층 최근접 DGND 노드 거리},\,D,\,1000\,\mu\text{m})$$

- 동축선 $L=\frac{\mu_0\ell}{2\pi}\ln(b/a)$ [7], [11]의 형태다.
- **[구현 차이]** $b$로 antipad 반경이 아니라 **최근접 GND 노드 거리**를 쓴다(중앙값 130 µm). 한 쌍의 via 루프(two-wire)라면 $\frac{\mu_0\ell}{\pi}\ln(s/r)$로 2배가 돼야 한다. 구현은 귀환 via가 둘러싼 동축으로 가정한 절반값이다.
- `VM:155-158`의 $L=0.2\,\ell_{mm}[\ln(4\ell/D)+1]$ nH는 Johnson & Graham [12]의 partial self-L 식($5.08h[\ln(4h/d)+1]$ nH, 인치)이다. 이 값은 **연구 모델에서 쓰지 않는다**(R만 사용).
- 평면쌍 via 배열 물리 모델은 Kim–Ren–Fan [13] 참고.
- 알려진 한계:
  - via–via 상호 L이 없다(Kim–Ren–Fan은 음의 상호 L도 가능하다고 보고).
  - via는 평면에 **셀 절점 1개로 접촉**한다. 5점 라플라시안 점원의 등가반경은 $r_{eq}=h\,e^{-\pi/2}\approx0.208h$다(Peaceman [14], EXP-7 `K7:11,63`). 따라서 접촉당 확산 L이 $\Delta N=\ln(r/0.208h)/2\pi$ square만큼 틀린다(fine 50 µm·PTH에서 +0.31 □). 병렬 접촉 수가 많아 포트 영향은 0.55%였다.
  - 패드 링크: 패드 footprint 안 같은 넷 노드를 $R=0.5/(\sigma t)$, $L=0$으로 잇는다(`M3:368`).

---

## 7. Decap 모델

SPICE `.SUBCKT`(Murata 래더, 최대 13단)를 파싱한다. R/L/C/K만 허용한다(`SP:451-482`). 1포트 $Z(f)$는 **MNA**(절점 + 인덕터 가지전류)로 계산한다(`SP:185-265`).

$$\begin{bmatrix}\mathbf G+j\omega\mathbf C & \mathbf A\\ \mathbf A^{T} & -j\omega\mathbf L\end{bmatrix}\begin{bmatrix}\mathbf v\\ \mathbf i_L\end{bmatrix}=\begin{bmatrix}\mathbf e_{+}\\ \mathbf 0\end{bmatrix},\qquad Z_{dec}=v_{+},\quad L_{ij}=k_{ij}\sqrt{L_iL_j}$$

- R/C 스탬프는 `SP:230-247`, 결합행렬은 `SP:216-223`, 가지 방정식은 `SP:249-251`에 있다.
- 채택 모델은 $Y_{dec}=1/Z_{dec}$를 레일 핀 절점과 이상 기준 사이에 스탬프한다(`M3:493-494`; GND 절점 −1 = 기준).
- ESR·ESL의 주파수 의존성: 래더의 각 가지 $R_k + j\omega L_k + 1/(j\omega C_k)$가 주파수마다 다른 몫의 전류를 나른다. 그래서 등가 $\mathrm{Re}\,Z(f)$(ESR)와 $\mathrm{Im}\,Z/\omega$(ESL)가 주파수에 따라 변한다. 저주파에서는 큰 C·큰 R 가지가, 고주파에서는 작은 L 가지가 우세하다. 유전 흡수·전극 표피효과를 이 RC/RL 사다리가 근사한다.
- port18 레일의 source-enabled decap은 **421개**다(1 µF 248, 100 nF 165, 10 µF 8; `results/exp1/EXP1_REPORT.md:15`).
- **[구현 차이]** 실장 루프(패드→via) L은 §6 요소로만 표현되고, 부품 바디–평면 사이 추가 루프 L은 없다.

---

## 8. 포트 정의

- Sigrity 포트는 positive pin group(DUT 범프, port18은 978개)과 negative pin group(DGND, 10,919개)이다.
- 구현: 모든 +핀이 스냅된 절점을 하나의 초노드로 합친다(`M3:271-273`). 1 A를 주입하고(`M3:507`), −핀은 이상 기준이다.
  $$Z_{port}=V_{P}\big|_{I_P=1\,\text{A}}$$
- 이는 pin group을 이상 short로 보는 가정이다. PowerSI의 실제 pin 결합 방식은 [미검증]이다([15], `reviews/proposal_fresh.md:100`).

---

## 9. 기준면(벽) 규칙 두 가지

**(a) Physical, GND-only** (`1b:102-155`)
- 셀마다 위/아래 각 3개 도체층 안에서 **DGND 아트워크가 그 셀을 덮는 첫 층**을 벽으로 삼는다(셀 단위, 이름상 층이 아닌 실제 도형).

**(b) Cavity-wall, PowerSI 관례 — 채택 기본값** (`R8:44-61`, 주입 `R8:129`)
- 레일 자신을 제외한 **모든 넷**의 금속 합집합으로 같은 탐색을 한다.
- 결과: L14 $d_{\text{eff}}$ 21.8→15.0 µm, L25 70.3→51.2 µm.
- 양측 $Z_s$ 과반 판정도 이 규칙으로 다시 평가한다.

**(b)가 물리적이지 않은 이유 [추론, `reviews/review_fresh_exp1.md` Q1]**
1. 떠 있는 타넷 평면은 판 사이에 있다. 레일–GND TEM 자계 $\mathbf H$는 그 시트에 **접선**이고, 유도 전계는 $\mathbf E=-\partial\mathbf A/\partial t=-j\omega\mathbf A$다. $\mathbf A\parallel\mathbf J_{rail}$이다.
2. 레일 전류는 DUT via(소스)에서 decap via(싱크)로 흐르는 퍼텐셜 흐름이다. 그래서 대부분 비회전($\nabla\times\mathbf J\approx0$)이고, 시트 위 $\mathbf E_t$도 대부분 기울기장이다.
3. 기울기장은 시트 표면 전하 재배치만으로 상쇄된다. **발산 0인 와전류 루프는 생기지 않는다**(Helmholtz 분해의 두 성분은 직교). 따라서 $\delta>t$인 시트는 자기적으로 투명하다.
4. 폐로 경로도 없다. 타넷 평면을 거쳐 GND로 돌아가려면 판간 C를 지나야 한다. DUT 창 약 160 pF ~ 판 전체 약 2 nF는 1 MHz에서 $|1/\omega C|\approx10^2$–$10^3$ Ω다. 필요한 이득 $\omega\cdot14$ pH ≈ 0.09 mΩ보다 6자리 크다.
5. 방향도 반대다. 물리적 차폐는 $\omega$에 비례해 고주파에서 커지는데, 필요한 보정은 저주파에서 크다.
- Ott [16]도 전원면이 귀환면이 되려면 decap이 경로를 이어야 한다고 기술한다.

**그런데도 채택한 이유(경험적)**
- EXP-8에서 예측 ΔL 변화와 실측 변화의 상관이 **0.965**였다.
- 7케이스 중 5개에서 ΔL이 ±3.3 pH 안으로 모였다.
- port18의 G3은 9.1%→2.7%, 260804 held-out은 10.0%→4.9%가 됐다.
- 이는 PowerSI hybrid solver [17], [18]가 벽을 넷과 무관하게 정의한다는 **도구 관례의 추정**이며, `.DropShapesOfUnselectedNets` 등 SPD 지시문(`reviews/sigrity_voids.md` §3)과의 관계는 [미검증]이다.
- 제품에는 두 모드를 명시적으로 노출한다(`DECISIONS.md` D5).

---

## 10. 검증용 해석해 (EXP-7)

무한 평면쌍에서 반경 $r_1$, $r_2$인 두 via가 간격 $s\gg r$로 떨어져 있으면, 2-D 선원 두 개의 퍼텐셜 차는 다음과 같다.

$$N_\square=\frac1{2\pi}\ln\frac{s^2}{r_1r_2},\qquad R=\frac{1}{\sigma t}N_\square=\frac{1}{2\pi\sigma t}\ln\frac{s^2}{r_1r_2},\qquad L=\mu_0 d\,N_\square=\frac{\mu_0 d}{2\pi}\ln\frac{s^2}{r_1r_2}$$

- 모델 edge가 $R$과 $L$에 같은 그래프 라플라시안을 쓰므로, 저주파 격자 해는 $N_\square$ 하나로 두 값을 모두 준다(`K7:3-7,62`).
- 출처: 2-D 확산저항·평면쌍 via 간 인덕턴스 [13], [19]. [19]의 해당 식 번호는 [미검증]이다.
- 결과(`results/exp7/EXP7_REPORT.md` §1, L25: d = 100 µm, t = 32 µm, 16×16 mm):
  - 노드 접촉 격자 $N_\square$는 $r_{eq}=0.208h$ 기준 해석해와 **+1.2 … −8%** 안에서 일치한다.
  - 해상 disc 접촉($r\ge h/2$)은 물리 $r$ 기준 +0.3 … +6.1%다.
  - 판정: 커널은 옳다. 차이는 격자 분산과 §6의 점접촉 등가반경 때문이다.

---

## 11. 천공 시트 유효 전도도 — 기각된 가설 (EXP-6)

정사각 배열의 절연 원형 구멍(면적비 $\varphi$)에 대한 2-D 최저차 결과(Rayleigh [20])는 다음과 같다.

$$\frac{\sigma_{\text{eff}}}{\sigma}=\frac{1-\varphi}{1+\varphi}\quad\Rightarrow\quad \frac{R_{\text{eff}}}{R}=\frac{1+\varphi}{1-\varphi}$$

- **코드에는 없다.** EXP-6 보고서 표에서만 계산했다(`results/exp6/EXP6_REPORT.md` §2; L25는 $\varphi=0.257$ → 1.69배).
- 가설은 "PowerSI가 작은 void를 채워 R·L이 함께 작아진다"였다. `exp6/run6.py:57-61`로 void를 직접 채워 검정했다.
- 결과: 530 µm까지 모두 채워도 ΔL/L_plane은 0.251→0.233으로 거의 불변이었다. R은 참조보다 낮아져 부호가 반대였다. 따라서 **기각**했다.

---

## 12. 참고문헌

1. D. M. Pozar, *Microwave Engineering*, 4th ed., Wiley, 2012, §4.3. ISBN 978-0-470-63155-3. [미검증: 판·절 번호는 기억 기반]
2. IBIS Open Forum, *Touchstone® File Format Specification, Version 2.0*, 2009. https://ibis.org/touchstone_ver2.0/touchstone_ver2_0.pdf (v1.1: https://ibis.org/connector/touchstone_spec11.pdf)
3. M. Swaminathan, A. E. Engin, *Power Integrity Modeling and Design for Semiconductors and Systems*, Prentice Hall, 2007. ISBN 978-0-13-615206-4. [장 번호 미검증]
4. A. E. Engin, K. Bharath, M. Swaminathan, "Multilayered finite-difference method (MFDM) for modeling of package and printed circuit board planes," *IEEE Trans. EMC*, 49(2):441–447, 2007. doi:10.1109/TEMC.2007.893331
5. J. H. Kim, M. Swaminathan, "Modeling of multilayered power distribution planes using transmission matrix method," *IEEE Trans. Adv. Packag.*, 25(2):189–199, 2002. doi:10.1109/TADVP.2002.803258
6. K. Bharath, J. Y. Choi, M. Swaminathan, "Use of the finite element method for the modeling of multi-layered power/ground planes with small features," *Proc. 59th IEEE ECTC*, 2009. https://ieeexplore.ieee.org/document/5074233/ [쪽·DOI 미검증]
7. S. Ramo, J. R. Whinnery, T. Van Duzer, *Fields and Waves in Communication Electronics*, 3rd ed., Wiley, 1994. ISBN 978-0-471-58551-0. [해당 절 미검증]
8. S. H. Hall, H. L. Heck, *Advanced Signal Integrity for High-Speed Digital Designs*, Wiley-IEEE Press, 2009. ISBN 978-0-470-19235-1, doi:10.1002/9780470423899. [해당 절 미검증]
9. I. Novak, "Lossy power distribution networks with thin dielectric layers and/or thin conductive layers," *IEEE Trans. Adv. Packag.*, 23(3), 2000. https://ieeexplore.ieee.org/document/861547/ [쪽·DOI 미검증]
10. H. A. Wheeler, "Transmission-line properties of parallel strips separated by a dielectric sheet," *IEEE Trans. MTT*, 13(2):172–185, 1965. doi:10.1109/TMTT.1965.1125962
11. C. R. Paul, *Inductance: Loop and Partial*, Wiley-IEEE Press, 2010. [미검증]
12. H. W. Johnson, M. Graham, *High-Speed Digital Design: A Handbook of Black Magic*, Prentice Hall, 1993. [via 인덕턴스 식 쪽 미검증]
13. J. Kim, L. Ren, J. Fan, "Physics-based inductance extraction for via arrays in parallel planes for power distribution network design," *IEEE Trans. MTT*, 58(9):2434–2447, 2010. doi:10.1109/TMTT.2010.2058278
14. D. W. Peaceman, "Interpretation of well-block pressures in numerical reservoir simulation," *SPE J.*, 18(3):183–194, 1978. doi:10.2118/6893-PA
15. SDSU, "Cadence PowerSI tutorial." https://electrical.sdsu.edu/_resources/files/cadence_powersi.pdf
16. H. W. Ott, *Electromagnetic Compatibility Engineering*, Wiley, 2009. ISBN 978-0-470-18930-6, doi:10.1002/9780470508510
17. Cadence Community, "What are the components of a hybrid solver in PowerSI?" (forum 57376). https://community.cadence.com/cadence_technology_forums/system-analysis/f/sigrity/57376/what-are-the-components-of-a-hybrid-solver-in-powersi
18. S. Farrahi, E. Koether, M. Mechaik, I. Novak, "Effect of power plane inductance on power delivery networks," DesignCon 2019. http://www.electrical-integrity.com/Paper_download_files/DC19_PAPER_Track11_EffectPowerPlaneInductancePDNs_Farrahi_.pdf
19. L. D. Smith, E. Bogatin, *Principles of Power Integrity for PDN Design—Simplified*, Prentice Hall, 2017. ISBN 978-0-13-273555-1. [해당 식 미검증]
20. Lord Rayleigh, "On the influence of obstacles arranged in rectangular order upon the properties of a medium," *Phil. Mag.* (5th ser.), 34(211):481–502, 1892. [DOI 10.1080/14786449208620364 미검증]

### 수식 → 코드 위치 → 출처

| 수식 | 코드 위치 | 출처 |
|---|---|---|
| $Z=Z_0(I+S)(I-S)^{-1}$ | `TS:273-298` | [1],[2] |
| $e=\lvert Z_m-Z_r\rvert/\lvert Z_r\rvert$, G1–G5 | `RE1:58-83`, `R3:36-54` | — |
| KCL 절점식, 1 A 주입 | `M3:465-508` | [3],[4] |
| $Z_{edge}=Z_s\ell/(wG)+j\omega\mu_0 d_e\ell/w_{\text{eff}}$ | `R4:75-88`(`M3:449-463`) | [3],[4],[5] |
| $Y_{sh}=j\omega C(1-j\tan\delta)$, $C=\varepsilon_0A/\sum t/\varepsilon_r$ | `1b:129-143`, `M3:479` | [4],[5] |
| $d_{\text{eff}}=d_{up}d_{dn}/(d_{up}+d_{dn})$ | `1b:140` | [3] (유도는 본문) |
| $Z_c\coth\gamma t$ → $1/\sigma t+j\omega\mu_0t/3$ | `MF:735-798` | [7],[8] |
| $\tfrac12Z_c\coth(\gamma t/2)$ → $\mu_0t/12$ | `R4:47-57`, `P5:42-62`, `MF:801-825` | [7],[9] |
| $L=\mu_0 d\,\ell/(w+2d)$ | `M3:288`, `R4:84-87` | [10] [미검증 계수] |
| $G_x=I\,n_x/n_y$ 창 균질화 | `HG:51-138` | — (자체 방법) |
| $R_{via}=\ell/(\sigma A)$ | `VM:79,128,152-154` | 옴의 법칙 |
| $L_{via}=\frac{\mu_0}{2\pi}\ell\ln(s/r)$ | `M3:307-308`, `M1:379` | [7],[11],[13] |
| $0.2\ell[\ln(4\ell/D)+1]$ nH (미사용) | `VM:155-158` | [12] |
| $r_{eq}=he^{-\pi/2}$ | `K7:11,63` | [14] |
| 디캡 MNA $Z_{dec}$ | `SP:185-265`, `M3:493-494` | 표준 MNA |
| 포트 초노드 | `M3:271-273,507-508` | [15] [미검증 관례] |
| cavity-wall 벽 규칙 | `R8:44-61,129` | [17],[18] (관례 추정) |
| $N_\square=\ln(s^2/r_1r_2)/2\pi$ | `K7:3-7,62` | [13],[19] |
| $\sigma_{\text{eff}}/\sigma=(1-\varphi)/(1+\varphi)$ | 코드 없음(`EXP6_REPORT.md` §2) | [20] |
