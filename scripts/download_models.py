# 이미지 빌드 때 문서 변환 모델을 미리 받아 둔다 (Dockerfile에서 실행).
# 레이아웃 모델은 변환기 초기화 때, OCR 모델은 첫 OCR 때 받아지므로 초기화만으로는 부족하다.
# 그래서 글자가 이미지로만 들어 있는 스캔 PDF를 실제로 변환해 OCR까지 태운다.
# 변환이 실패하면 예외가 나서 빌드도 같이 실패한다.
from io import BytesIO

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter
from PIL import Image, ImageDraw

image = Image.new("RGB", (1240, 1754), "white")
ImageDraw.Draw(image).text((120, 120), "model download", fill="black", font_size=36)
pdf = BytesIO()
image.save(pdf, "PDF", resolution=150)
pdf.seek(0)

DocumentConverter().convert(DocumentStream(name="model-download.pdf", stream=pdf))
