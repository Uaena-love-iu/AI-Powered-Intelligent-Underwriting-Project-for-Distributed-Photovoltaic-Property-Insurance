"""Human-reviewed metadata keyed by exact file bytes; never fuzzy model overrides."""

VERIFIED_DOCUMENT_METADATA = {
    "d930fcbdf24b04573601c495e5b090c7862283311f398bbe8815e0064cac844d": {
        "maximum_system_voltage_v": 1000,
        "page": 2,
        "quote": "Maximum System Voltage 1000V DC",
        "source_url": "https://grismzgqep82cpmz.public.blob.vercel-storage.com/datasheets/panels/ja-solar/jam66d42-mb-605w.pdf",
        "verification_method": "human_visual_review_of_rendered_pdf",
        "review_date": "2026-10-11",
        "note": "OCR将图片中的1000V误读为1500V；只对该SHA256文档使用人工核验结果。",
    }
}
