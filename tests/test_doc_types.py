"""临床开发文档类型已进入 LinkML 枚举。"""

from biomed_ontology._generated.hmd_fact import DocTypeEnum


def test_ib_and_csr_doc_types_exist():
    assert DocTypeEnum.INVESTIGATOR_BROCHURE.value == "INVESTIGATOR_BROCHURE"
    assert DocTypeEnum.CLINICAL_STUDY_REPORT.value == "CLINICAL_STUDY_REPORT"
