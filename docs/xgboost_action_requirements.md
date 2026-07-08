# XGBoost Action 준비 기준

## 문서 목적
- 이 문서는 `edge/models/xgboost_action.json`을 만들기 위해 필요한 입력, 라벨, 산출물, 주의점을 정리한 참고 문서다.
- 현재 프로젝트의 1차 AI는 `YOLO26s-pose -> feature 추출 -> XGBoost 행동 분류` 흐름을 목표로 한다.
- 아직 `xgboost_action.json`은 없고, 현재 코드는 heuristic fallback으로만 동작한다.

## 1. `xgboost_action.json`이 의미하는 것
- `xgboost_action.json`은 `XGBoost`로 학습이 끝난 행동 분류 모델 파일이다.
- 현재 코드에서는 [action_classifier.py](C:/Users/jju03/Desktop/university/program development/elderly_care_ai/edge/action_classifier.py)에서 이 파일을 읽는다.
- 이 모델은 `YOLO26s-pose`가 뽑은 사람 keypoints 자체를 직접 저장하는 것이 아니라, keypoints로부터 계산된 `feature vector`를 입력으로 받아 행동 라벨을 예측한다.

## 2. 현재 구조에서 필요한 입력 데이터

### 2.1 필수 파일
- `mp4` 원본 영상
- 영상과 1:1로 매칭되는 라벨 파일
- 영상별 train/valid/test 분할 목록
- 라벨 정의표

### 2.2 현재 코드가 사용하는 feature
- keypoint 정규화 좌표 `17개 x/y`
- `bbox_center_x_norm`
- `bbox_center_y_norm`
- `bbox_width_norm`
- `bbox_height_norm`
- `bbox_aspect_ratio`
- `pose_confidence_mean`
- `visible_joint_ratio`
- `torso_angle_deg`
- `shoulder_tilt_deg`
- `hip_tilt_deg`
- `left_knee_angle_deg`
- `right_knee_angle_deg`
- `left_hip_angle_deg`
- `right_hip_angle_deg`
- `center_velocity_px_s`

현재 column 정의는 [feature_extractor.py](C:/Users/jju03/Desktop/university/program development/elderly_care_ai/edge/feature_extractor.py)에 있다.

## 3. 가장 먼저 결정해야 하는 라벨 목표

### 3.1 현재 1차 AI 기본 라벨
- `STANDING`
- `SITTING`
- `LYING`
- `WALKING`
- `TRANSITION`
- `UNKNOWN`

### 3.2 현재 받은 라벨 파일의 성격
- 현재 테스트 라벨은 `ABNOR_H`, `H11H22H31`처럼 `이상행동 이벤트` 라벨이다.
- 이 라벨은 현재 1차 AI의 자세 라벨과 직접 같지 않다.

### 3.3 중요 주의점
- 지금 받은 이벤트 라벨만으로는 현재 목표인 `자세/행동 상태 분류기`를 바로 잘 만들 수 없다.
- 이유는 현재 라벨이 `이 구간은 이상행동 이벤트다`를 뜻하지, `이 프레임은 서있음/앉음/눕기/걷기`를 직접 주지 않기 때문이다.

## 4. 권장 학습 방향

### 4.1 1차 권장안
- `XGBoost`는 먼저 `기본 행동 상태 분류`에 사용한다.
- 목표 라벨:
  - `STANDING`
  - `SITTING`
  - `LYING`
  - `WALKING`
  - `TRANSITION`
  - `UNKNOWN`

### 4.2 이후 확장안
- 이상행동 이벤트 라벨은 2차 AI 또는 별도 시퀀스 분류기로 처리한다.
- 즉 현재 구조는 다음처럼 나누는 것이 맞다.
  - 1차 AI: `자세/행동 상태`
  - 2차 AI: `정상/이상/위험 이벤트`

### 4.3 예외적 대안
- 만약 `ABNOR_H/H11H22H31` 같은 라벨을 1차에서 직접 분류하고 싶다면, 프레임 단위 단일 pose가 아니라 `짧은 구간(window)` 기반 feature가 추가로 필요하다.
- 예:
  - 최근 1~3초 torso angle 변화량
  - hip/knee angle 변화량
  - bbox 중심 이동 변화량
  - pose confidence 변화량
  - lying 유지 시간
- 이 경우 현재 단일 프레임 `XGBoost`보다 `window feature + XGBoost` 또는 `2차 AI`가 더 맞다.

## 5. 학습에 필요한 실제 산출물
- `dataset_manifest.csv`
  - 영상 파일 경로
  - 라벨 파일 경로
  - split(train/valid/test)
- `pose_features.csv` 또는 `pose_features.parquet`
  - 프레임별 feature vector
  - 라벨
  - frame index
  - source video
- `label_map.json`
  - 정수 id와 문자열 라벨 매핑
- `xgboost_action.json`
  - 학습 완료 모델
- `metrics.json`
  - accuracy
  - macro f1
  - confusion matrix

## 6. 학습 전에 확인할 것
- 라벨이 `행동 상태 라벨`인지 `이벤트 라벨`인지 구분
- mp4와 라벨 파일이 정확히 매칭되는지 확인
- 카메라 시점이 천장 한구석 기준으로 학습/테스트에 일관적인지 확인
- `YOLO26s-pose` skeleton 품질이 충분한지 먼저 확인
- 가려짐/어두움 상황에서 pose confidence가 과도하게 무너지는지 확인

## 7. `yolo26s-pose.pt`의 의미
- `yolo26s-pose.pt`는 `YOLO26 small pose` 사전학습 가중치 파일이다.
- 이 파일에는 사람이 있는 위치와 사람의 keypoints를 추정하기 위한 신경망 파라미터가 들어 있다.
- 즉 이 파일은 `행동 분류 모델`이 아니다.
- 이 파일의 역할은 다음과 같다.
  - 사람 검출
  - 사람 bbox 추정
  - 사람 keypoints 추정
- 이후 `XGBoost`는 이 keypoints 기반 feature를 받아서 행동 상태를 분류한다.

정리하면:
- `yolo26s-pose.pt` = pose 추출기 가중치
- `xgboost_action.json` = pose feature를 받아 행동 라벨을 내는 분류기

## 8. 현재 라벨 비교 테스트 결과 해석
- 매칭된 라벨 파일:
  - `FD_In_H11H22H31_0001_20201016_20.json`
- 매칭된 영상:
  - `FD_In_H11H22H31_0001_20201016_20.mp4`
- 라벨 구간:
  - `startFrame = 8062`
  - `endFrame = 8123`
- 평가 결과 파일:
  - [eval_fd_in_h11h22h31_0001_step1.json](C:/Users/jju03/Desktop/university/program development/elderly_care_ai/temp_test/eval_fd_in_h11h22h31_0001_step1.json)

요약 결과:
- 샘플 프레임 수: `62`
- 사람 검출 성공 프레임 수: `23`
- 검출 비율: `0.371`
- 평균 pose confidence: `0.7888`
- 현재 행동 출력: 전부 `LYING`

중요:
- 이 결과는 `이상행동 정확도`를 뜻하지 않는다.
- 현재는 `xgboost_action.json`이 없어서 heuristic fallback이 `LYING`을 출력하고 있기 때문이다.
- 따라서 지금 비교 가능한 것은 `라벨 구간에서 pose가 어느 정도 잡히는지`까지다.

## 9. 지금 바로 필요한 다음 작업
1. 1차 XGBoost의 목표 라벨을 `행동 상태`로 고정
2. feature export 스크립트 작성
3. 학습용 CSV/Parquet 생성
4. train/valid/test 분할
5. `XGBoost` 학습
6. `edge/models/xgboost_action.json` 저장
7. 현재 평가 스크립트로 학습 전/후 비교
