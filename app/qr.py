"""사진 속 QR 코드 읽기. 동아리 신청서나 일정 링크가 담긴 QR 사진을 보내면 그 내용을 대화 입력창에 넣어 준다.

Python 라이브러리(pyzbar)는 requirements.txt 로 설치되지만, 실제로 QR 코드를 읽는 zbar 는
운영체제에 따로 설치하는 프로그램이다. 노트북에는 직접 설치하고, 서버에서는 실행 환경(이미지)에 들어 있어야 한다.
"""
import io

from app.logs import log

MAX_BYTES = 5 * 1024 * 1024  # 사진 한 장 최대 5MB


class QrError(Exception):
    pass


def read_codes(data: bytes) -> list[str]:
    from PIL import Image, UnidentifiedImageError

    if len(data) > MAX_BYTES:
        raise QrError("사진이 5MB보다 큽니다.")
    try:
        # pyzbar 는 불러오는 순간 zbar 프로그램(공유 라이브러리)을 찾는다. 없으면 ImportError 가 난다.
        from pyzbar import pyzbar
    except ImportError as error:
        log("ERROR", "zbar 프로그램을 찾을 수 없습니다. 실행 환경에 zbar 가 설치되어 있지 않습니다.")
        raise QrError("사진에서 QR 코드를 읽는 프로그램이 서버에 없습니다.") from error
    try:
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError as error:
        raise QrError("사진 파일을 읽을 수 없습니다.") from error
    codes = [code.data.decode("utf-8", errors="replace") for code in pyzbar.decode(image)]
    if not codes:
        raise QrError("사진에서 QR 코드를 찾지 못했습니다.")
    log("INFO", "QR 코드 읽기", count=len(codes))
    return codes
