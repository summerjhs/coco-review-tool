# Mask Review Tool v2

COCO JSON 어노테이션 검수 웹 도구. 이미지와 JSON 파일을 매칭하여 시각화하고, 객체별 오류 메모를 남긴 뒤 엑셀로 내보낼 수 있습니다.

## 주요 기능

- **이미지 오버레이 시각화**: COCO JSON 어노테이션을 이미지 위에 렌더링
  - ROI: 점선(dashed) 표시
  - 기타 카테고리: 반투명 컬러 마스크 + 윤곽선
  - 카테고리명, barcode, shipment_id 등 속성값 라벨 표시
- **객체 클릭 검사**: 이미지에서 객체 클릭 시 속성 정보 확인
- **오류 메모**: 객체별 오류 내용 기록, 누락 객체도 클릭 위치와 함께 메모 가능
- **엑셀 내보내기**: 메모를 크롭 썸네일과 함께 XLSX로 추출
- **크롭 기반 검수**: 바코드/카테고리별 그룹화, 크롭 이미지 비교 (기존 모드)
- **성능 최적화**: 이미지 다운스케일 렌더링, 오버레이 캐시, 프리페치

## 설치

```bash
pip install -r requirements.txt
```

### 요구사항

- Python 3.9+
- 주요 라이브러리: Flask, OpenCV, pycocotools, openpyxl, waitress

## 실행

```bash
python app.py
```

브라우저에서 접속:
- **이미지 리뷰**: http://localhost:5000/review
- **크롭 검수**: http://localhost:5000

## 사용법

### 이미지 리뷰 모드 (`/review`)

1. **Browse** 버튼으로 이미지 폴더 선택 (하위 `annotation/` 폴더에 COCO JSON 필요)
2. **Load** 클릭하여 데이터셋 로드
3. 좌측 이미지 목록 또는 `Prev`/`Next` 버튼으로 이미지 탐색 (화살표 키 지원)
4. 이미지에서 객체 **클릭** → 우측 패널에서 속성 확인
5. 오류 발견 시 메모 입력 후 **저장** (`Ctrl+S`)
6. 빈 공간 클릭 → 누락 객체 메모 가능
7. **Export XLSX** 버튼으로 메모를 엑셀 파일로 다운로드

### 데이터 형식

```
dataset_folder/
  ├── image1.jpg
  ├── image2.jpg
  └── annotation/
      └── instances_default.json   # COCO format
```

COCO JSON 구조:
```json
{
  "images": [{"id": 1, "file_name": "image1.jpg", "width": 3840, "height": 2160}],
  "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "segmentation": {...}, "bbox": [...], "attributes": {"barcode": "...", "shipment_id": "..."}}],
  "categories": [{"id": 1, "name": "item"}, {"id": 9, "name": "roi"}]
}
```

## 프로젝트 구조

```
mask_review_v2/
  ├── app.py              # Flask 라우트 + API 엔드포인트
  ├── coco_utils.py       # COCO 데이터 로딩, 렌더링, 히트테스트
  ├── export_utils.py     # XLSX 내보내기
  ├── requirements.txt
  ├── templates/
  │   ├── index.html      # 크롭 검수 페이지
  │   └── review.html     # 이미지 리뷰 페이지
  └── static/
      ├── app.js
      └── style.css
```
