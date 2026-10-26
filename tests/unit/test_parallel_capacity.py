from threading import Barrier

from app.contracts import MaterialCategory, WatermarkStatus
from app.pipeline import UnderwritingPipeline
from app.providers.common import ProviderError
from app.providers.ocr.tasks import extract_tasks
from unit.test_staged_pipeline import CompleteFilingOcr, CompleteVision, certificate, image, project


def test_ocr_tasks_overlap_and_keep_failure_isolation():
    rendezvous = Barrier(2, timeout=3)

    class Ocr:
        attempts = {'watermark': 0, 'business': 0}

        def extract(self, item):
            self.attempts[item.ocr_task] += 1
            if self.attempts[item.ocr_task] == 1:
                rendezvous.wait()
            if item.ocr_task == 'business':
                raise ProviderError('unavailable')
            return []

    item = image('nameplate', MaterialCategory.COMPONENT_NAMEPLATE)
    item.material.watermark_status = WatermarkStatus.PRESENT
    fields, details = extract_tasks(Ocr(), item)
    assert fields == []
    assert [(d['task'], d['status']) for d in details] == [
        ('watermark', 'success'), ('business', 'failed'),
    ]


def test_geometry_and_component_overlap_after_front_checks():
    rendezvous = Barrier(2, timeout=3)

    class Vision(CompleteVision):
        def estimate_geometry(self, materials, installation):
            rendezvous.wait()
            return super().estimate_geometry(materials, installation)

    class Component:
        name = 'component-test'

        def lookup(self, model):
            rendezvous.wait()

    pipeline = UnderwritingPipeline(ocr_provider=CompleteFilingOcr(),
                                    vision_provider=Vision(), component_provider=Component())
    case = pipeline.run(case_id='parallel', project=project(component_model='TEST-5400'),
                        materials=[certificate(), image('roof')])
    assert case.pre_catastrophe_outcome == 'continue'
    assert [t.step.value for t in case.processing_trace if t.step.value in {
        'geometry_group', 'component_lookup',
    }] == ['geometry_group', 'component_lookup']
