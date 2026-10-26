from datetime import UTC, datetime

import pytest

from pv_component_lookup.contracts import ComponentProfile, numeric_missing_fields
from test_workbench import bench


def mechanical_profile(**kwargs):
    return ComponentProfile(
        component_model='JAM66D42-580/MB', manufacturer='JA Solar', source_name='API fixture',
        retrieved_at=datetime.now(UTC), match_confidence=1,
        front_static_load_pa=5400, back_static_load_pa=2400,
        parameter_evidence={
            'front_static_load_pa': [{'status':'found','value':5400,'source_kind':'api','raw_label':'maxLoadFrontPa','source_url':'https://www.jasolar.com/test.pdf','load_basis':'test'}],
            'back_static_load_pa': [{'status':'found','value':2400,'source_kind':'api','raw_label':'maxLoadRearPa','source_url':'https://www.jasolar.com/test.pdf','load_basis':'test'}],
        }, **kwargs,
    )


def test_mapping_retains_raw_identity_source_and_unverified_basis():
    result=mechanical_profile().workbench_payload()
    assert result['wind_load_pa'] is None and result['snow_load_pa'] is None
    assert result['ui_fields']['wind_load_pa']['value']==2400
    assert result['ui_fields']['snow_load_pa']['value']==5400
    for key in ('wind_load_pa','snow_load_pa'):
        field=result['ui_fields'][key]
        assert field['status']=='mechanical_mapped'
        assert field['value_origin']=='mechanical_mapping'
        assert field['direct_value'] is None
        assert field['load_basis']=='unspecified'
        assert not field['can_use_as_design_capacity']
        assert field['evidence'][0]['source_url']=='https://www.jasolar.com/test.pdf'
    assert result['parameter_tasks']['missing_values']==[]


@pytest.mark.parametrize('identity',['series_only','ambiguous'])
def test_no_mapping_from_unconfirmed_identity(identity):
    result=mechanical_profile(model_match_status=identity).workbench_payload()
    assert all(f['value'] is None for f in result['ui_fields'].values())
    assert len(result['parameter_tasks']['missing_values'])==2


@pytest.mark.parametrize('field',['back_static_load_pa','wind_load_pa'])
def test_conflicting_evidence_cannot_be_hidden_by_mapping(field):
    profile=mechanical_profile()
    profile.parameter_evidence[field]=[{'status':'conflict','value':4000}]
    result=profile.workbench_payload()
    assert result['ui_fields']['wind_load_pa']['value'] is None
    assert result['ui_fields']['wind_load_pa']['status']=='conflict'
    assert 'wind_load_pa' in result['parameter_tasks']['missing_values']


def test_explicit_parameter_takes_priority_over_direction_mapping():
    profile=mechanical_profile(wind_load_pa=1600)
    profile.parameter_evidence['wind_load_pa']=[{'status':'found','value':1600,'source_kind':'datasheet','load_basis':'design'}]
    field=profile.workbench_payload()['ui_fields']['wind_load_pa']
    assert field['value']==1600 and field['status']=='found'
    assert field['value_origin']=='direct_reported' and field['load_basis']=='design'


def test_agent_searches_only_missing_numbers_and_carries_verification_tasks(bench):
    class Provider:
        def lookup(self,model): return mechanical_profile()
    class Agent:
        def run(self,model,brand,missing,emit,**kwargs):
            pytest.fail('风雪齐全时不应因缺少冰雹启动Agent')
    bench.provider_factory=lambda index:Provider()
    bench.agent_factory=lambda:Agent()
    result=bench.run('JAM66D42-580/MB')
    assert result['status']=='parameters_mapped'
    assert result['agent'] is None
    assert result['profile']['ui_fields']['wind_load_pa']['value']==2400


def test_complete_mapped_measurements_do_not_launch_duplicate_search(bench):
    class Provider:
        def lookup(self,model): return mechanical_profile(hail_resistance_mm=25)
    bench.provider_factory=lambda index:Provider()
    bench.agent_factory=lambda:pytest.fail('No missing number; must not launch Agent')
    result=bench.run('JAM66D42-580/MB')
    assert result['status']=='parameters_mapped'
    assert result['agent'] is None
    assert result['profile']['parameter_tasks']['missing_values']==[]
    assert len(result['profile']['parameter_tasks']['verification_targets'])==2


def test_no_zero_or_unknown_mechanical_value_counts_as_complete():
    assert numeric_missing_fields({'front_static_load_pa':0,'back_static_load_pa':None,'hail_resistance_mm':25})==['wind_load_pa','snow_load_pa']
