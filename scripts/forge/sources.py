"""Forge sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import os
import re

from . import CLI_PATH, verify_sources
from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import secure_io as _secure_io
from . import storage as _storage

# BEGIN TEST MANIFEST
TEST_MANIFEST = {
    "conftest.py": "35e40c2f68e0d304044cdf607f997597fdddcf18ad1f8f6b018152cc2266f5c3",
    "detached_test_support.py": "5deabb6b1dadbc155d450981e2836e730574f41f83e3feb53d7d462681467295",
    "fault_injection_support.py": "83597a7136891f9ee6cbb38603f7e2179c602ffcbae3cf307bdeec47dc88811b",
    "forge_test_helpers.py": "94180fbdb20ebefca347f1969cf04a3f063281ed7ccc02f3f02eea8aea414db4",
    "native_admission_fixtures.py": "f12349f1326f08096006ebf6e84fd8602c3962cfb748a3a3938a2d11ef968790",
    "native_reservation_fixtures.py": "86cd4c6a4d0359b74629e29e8a4580dcfe051e57fdfe6b856105ea4a7921cd9a",
    "runtime_support.py": "1317eecfdb7a424b0cecefe808dce719b9d2a102a30360ef813e049f16751699",
    "test_adapter_loading.py": "d8c4086464c3cd401caa2d6887b6f4ebbb44d863620f02086507d90da60a8b7c",
    "test_benchmark_forge.py": "1436b31c5fc07824a903fb4a2f5bccf565059dbffa67151da7819b932c2383eb",
    "test_cache_publication.py": "218df403a40b42fe170ee816d0dd5de2df7f58106ac79e7d4dd55983f0698aff",
    "test_capabilities.py": "1d30df82e81ab304ae6c11991a9290bd34385bd533176704b56853243218d1e5",
    "test_ci_coverage.py": "6b8c404d8876ecc04cfbe096777444f0fd80e434da1a2fe1d68fba860e211193",
    "test_claude_plugin.py": "6f4f96ba9dc7d1593da1887250afe52ce28090137a5fe213e7053550db2a1b46",
    "test_cli_helper_timeouts.py": "4ffeddf9de6eb7b8d794b6891414d204eaa3f5f71869876398e14dfca5878a5d",
    "test_coding_trials.py": "21bc89c8fd5023cfbadc664842c7c5f269b1d4088bf1297fc545956b033e2407",
    "test_compact_evaluations.py": "0b6c8c448711a03e7a6586fece8c99f18fd4ff902c6399af6977511f7346f9d1",
    "test_compact_helpers.py": "cd01296d153b48d035cc31b4a95b9ca5536f963abe8e9e8cdbdd5438e214d1a8",
    "test_compact_plan.py": "bcdc03a4016c7ba2aaee540c7fc1aa3338c3fd9dea2f825d5efa274e5e7780e9",
    "test_compatibility_admission.py": "29c32ea4863b20cca87a95dfc9a85860fe546a0eea0dc3fdb00054dc8396a529",
    "test_compatibility_checks.py": "a781a8553250b42ef78c306acd21f80d31948491b6a0ed188fed0dd06a17fc74",
    "test_compatibility_workflows.py": "e2d20abc0c16b5e3bbb4713485cfe0729acd6e3085ffda90dd86ecd1671c830f",
    "test_complete_plan_admission.py": "388d6949bcb8b93079acb4c4a6aa33363bae663ff3ef34091108a0f4903870e3",
    "test_completion_contracts.py": "00bc8b153463486b062ceae517d7e8499a510643dcf9120296e48631c87fed24",
    "test_completion_records.py": "616a199a130e03f1210c010a76d674d5527f19cf05d1bd7a17ea3caf8259f9e2",
    "test_context_packets.py": "2cb431f1607d3f4f1acd0d8fc5aeb20cbecbab313c20ad7590c94b1f9db689e2",
    "test_context_usability.py": "4f38a491b9402e89ffacf8cd539449bcfdb565bab64e8a8f39b6d012296c15bf",
    "test_controlled_trials.py": "91e9f91bc55a4601c7ae1db5ccd362a19764a035528957435af0625c839493ed",
    "test_cross_host_resume.py": "b4308e20b080cb61bbce8f2a569104d7d26a0217de734630f9c2847ee1e62958",
    "test_detached_authorities.py": "9df8993eebb931d4211201f4d2d1b76fc5f35f64e147dcb7aeb4d087d8b47d3b",
    "test_detached_dependencies.py": "8fb4070e2a9507c0d3248b0e047525cb35728bd08be846584fbc7339828f1878",
    "test_detached_locks.py": "dcbdc69d937f92b0ccf98661ec5c59bc5591221a676263aecb8fa4a8c2d83e2a",
    "test_detached_recording.py": "9801dc1698bb642a9b0dda4f7e18ff699a490f9de83b0f2c430a2e699cb6915e",
    "test_detached_recovery.py": "ec67066cbf4f0c505aa5573d2848bd0f332fc11a8ad0a82359dab36b5f09b9c0",
    "test_detached_resources.py": "87b343b4576d73408a641313eb881ce25ec76a2e22185121d9943abaddd617b7",
    "test_detached_setup.py": "c60606369d002b64c9b4c8cbc9c904f97eabed46c75a4ed39f8410c5d4814ba2",
    "test_directory_observations.py": "8a444d00cf2273f5e32f0722de4efe23b008e22e325ac8c5f431f2f62e2692fa",
    "test_evaluations.py": "5a4f4ce677bcb8e4f1af7fa74e24f8e4b01b56f665c1621c3dec44c37458ee7f",
    "test_evidence_discovery.py": "1e2671eacebdbadda57d9f87a4570d7e1eb633574b8a01c78690d75d7bbcf0ce",
    "test_gate_reports.py": "4986d356f1261f95771993ea10a82956895f11313b595f302766d04868317b4f",
    "test_handoff_authority.py": "f8915d3bccbb20751c48ae3c95bd80bea29389691a76fa054c4507bfd519b09f",
    "test_handoff_failures.py": "228106db7ad96d04057647fffce839721b449e8a2884626ba55eea7264beb6f0",
    "test_handoff_readiness.py": "b1eb9f207136d7d397887b26a6a2fa204493f89ec3cf56e641e02412ac7b5925",
    "test_handoff_transport.py": "104517f15aa1c085e9da2fd45bb104ea1f021f77b851cb5e8fb7d592948e8cae",
    "test_handoff_wire.py": "4c2fb34168a5dae808b37dfd9d75840da93dc9ee93bc73200db80b90c7e27b72",
    "test_heldout_contracts.py": "fcca3c9af0a5cb85ce741015477c8d9a6555b47de45a2f061819a12002a850ab",
    "test_implementation_drift.py": "12f4cb0977c8557718015851ff1ae1a8bd7d305957d8b8e205942e453ba1fe7a",
    "test_implementation_phase_reuse.py": "f79a211e105becb4c23da1ea06a842d2fe1617bbb99fc1644195b7a6d13167db",
    "test_install_config_safety.py": "7214d15f4410b4b5d49bf1be558c83c2e713d2c2d7bb2c90f776814b53d247ba",
    "test_installation.py": "47a75c56bf2946ac30160cce8934bde62eaad1f24e2b68ae09489f3a48b4f32b",
    "test_installation_contract.py": "5497fbfd70310b238e2cc1e0206c51529ce2702f286afadd8243f4485f400457",
    "test_interview.py": "8e32056374b22a4247f916c3da205ceebd084ce66da0d79a2edebf4f0df4198b",
    "test_invoice_contract.py": "2e3ca21e6dd00c8efd134e66658cd2efe1816b32cc95ac41f78dcf3632832f23",
    "test_mutable_state.py": "c2a1bd08ce23094d4001a7e1998beac79c4c4ecf06f39dcf84a821ad86287b9e",
    "test_mutable_state_safety.py": "03b956534c4837d187dc19e43e99d673c1004a4ed7628e92abbf80c87f944f25",
    "test_native_package_identity.py": "46dd9530e9e3dfdb597741eea5bc7c7c465a355262d8952feb46c9df322be201",
    "test_native_process.py": "5757b225cc4aac67c81fc61b0e5f498d3ab2364c9ef0c76d7a0760bbfc84c07f",
    "test_native_workflow_trials.py": "b3e45332b47ff0bf244945e1e7fe0b0b135105d8cc7caf901de7420b9e426a48",
    "test_owned_verification.py": "c23850c286dfb8cda322fe445e51b1935836af7bf8bed1ff842cc497c63dbb4b",
    "test_parallel_ownership.py": "e0265620b989847bb0dc8cf60d8d5d12f62941a357c145d8da7e21cb0b2a1224",
    "test_parsed_plan_reuse.py": "eb023236aa41d1d6d2308ad0c1d371701b574ad7e0f8470558fd1407c73117b6",
    "test_patch_scope.py": "82bd87e4c55bc14b72966437b3b1c223c976157b9048262f01663b6a8d0161f6",
    "test_pinner_reads.py": "ebaf66728c0dabbf4c47d0bb67e8d634b04cf8542bf0fead02227973690cd32b",
    "test_plan_modes.py": "d01941dac0c93fb6a96f26fa594ccf4055854151ad9c2fc156bcae931e95e33f",
    "test_planning_contract.py": "a538b7e7435b97a800412ecb7e229dc0d5610c42f6852bf8d334cf2a1fefe80d",
    "test_portable_gate_batch.py": "a6846a59a8fd19bbf0b3de1f31b44193a7a75e84bc9f9a6eb266087587fd753d",
    "test_pretty_workflows.py": "2ddb457d800eda419a31720369c644efba63882ce2a1323910f8cdcbdace53fe",
    "test_project_commands.py": "b65902d31a3e3a0ead83dbe54e54f95e1e22c78135c0b2ee4b015c04b287aae9",
    "test_project_contracts.py": "c7d695bb85222caa6c68a555f34a2de30e378f8ff04f43f23a58b905095d5af3",
    "test_project_session_validation.py": "4b0df28adbb0817d732f2b5e250c26727a15785fd9f31c2a5a2498e9de8cf3ca",
    "test_provider_output.py": "52536a946a6abb86eb0f8b229c6a281c5285b648d5e7ce7772bd658a0ffd04c4",
    "test_provider_preflight.py": "a3fa436618ef71de5f5afbec5abff4831af0e0b3e4c4bb2456e5e1540fab872e",
    "test_provider_recovery.py": "337f949247894e4d2c32edd1fc1377a35a5f71429dcf7cdb66569f5cc693b60f",
    "test_providers.py": "937617e1400a89957435aafe669ee1a2b39107c7abad012182df4e847b344306",
    "test_quality_gates.py": "4b6bdb5dc9b982bc30a73c6e3354ae62b5e623bb196e4eff7b7513b97510a147",
    "test_release.py": "d16f0ad9b4997bf97bf5b7115fce6c69fc1e81644a5e3f99807fee4cec740d55",
    "test_release_identity.py": "2a2bbbd03bc07e7ece8b6c5f1f4c6ae7d271b7bfa41a0f8fe218da14c2e232db",
    "test_resume_context.py": "5aa47690740f0d1db589d1ea526f99c54de7541ae038641967aae610cc2df9b7",
    "test_resume_guidance.py": "40a776c2b9369e0b2fc0902ff4b287b7211639b5007f1f2d9d7ce66cb9f91bba",
    "test_retry_trial.py": "7dc38231f6157837bf5c4f6c428bb974c84971415cd55d2de751077559194a74",
    "test_runtime_binding.py": "5f10520b26f31e8c5af87ceb65b72e11ef2ac5807e98902620e90fbede6989b2",
    "test_runtime_lifecycle.py": "ffd28bc6c15c09565afe3deb4fc89405598d925886bb7be659c52a7065dc4f08",
    "test_runtime_loading.py": "b8b77461891f3cad76e1f5842a838d6f408f7b8e8e0e1fc45b13d003b95e668a",
    "test_runtime_performance.py": "4f7cf467082e3eca7b2548dff0a7aa6239270c9118e0f5f8a2cd6a5bc084a5d7",
    "test_runtime_reuse.py": "91fe91c5ccbd9077fc4965fd3d2f547ef683a69d524ceddb654e801e36425856",
    "test_section_contracts.py": "de911d33ee7d8e71ba372fe540e476aac41663cce4cfc7c3a9ec100b3943ca6d",
    "test_team_collaboration.py": "75cfcb0a4cc8764a09466c3af86b4611769ddf4d16861e2a8274bdb2a959c17e",
    "test_team_config.py": "c9d28c024567afe132bad6004a4a9e94412c89e3e87e3573564f50268db2e2ef",
    "test_team_git.py": "5014b7422b5d268e0a9bd71978cf1afc4a455cfe6e10025d4fed684d38deb622",
    "test_team_guidance.py": "6873006837f8ccea1c96d78275edb2a26f69488b302b47526019ec05efa7aeb9",
    "test_team_plan_claims.py": "4b895c63de64d3330a4f64df8678ab645be50e879851de1c341d66df85388bcb",
    "test_team_plan_integration.py": "82008dbd1206b31f6f533c46cacf1ccaedb8f18901a1ffad89354c94b2ea0daa",
    "test_team_plan_workflows.py": "e637547ccad14470f4e72b6e3650599bae57fd14c46116ec21e5ac8d419efdd5",
    "test_team_plans.py": "866bf251a05fd3da28f8cc5abd96761427144410a09f55da8d9fc0105a4534c7",
    "test_team_state.py": "748f3d1d93e48b6d54440d03f976167bc64acc1ee00fa53779cc3bdea337821a",
    "test_team_workflows.py": "e95e9187f23100fd485cf8152bb66a21704d998d6cccaaec0ab398a581b64338",
    "test_test_binding.py": "d08862ff2eee423190dd6878e12da54b2af6e47080848f1655a14cbe703a7723",
    "test_transaction_publication.py": "fc44fe564cc1641b4bbfd53e2c5bb95dbb637d1e27bc115ada4b634cf28b64f9",
    "test_transaction_rollback.py": "cdab79a27436bf25551a604b61d3fda77d0290de80f41214e0b5faa13e5f86f5",
    "test_trial_hosts.py": "80e67273c9a9ecf9ba173da01f97942cec4d731d6130579b78d68bb10a93fa4a",
    "test_trial_matrix.py": "54520d8161d2ec6afd20f558782cd074d7149bd7cbd89c6cc6a5c4177210ba34",
    "test_trial_planning_roots.py": "e8968fb283b0de99963020fbdba3778d15dc7f72550fa893e3e9821ff4d4ab16",
    "test_trial_realism.py": "f14d4364d3525b2221e9d2ce9a814a0a671e290871a995ab300affdb597b28a7",
    "test_trial_suite_attempt_lineage.py": "1d18e5011fbb2a265e8b40d48df74bca4cacb9f898f555ef70967b3ed66ef6d9",
    "test_trial_suite_cancellation.py": "db5a6857af0a6d5673d228535e8479ab1bc5ebf09db61a1e79e254d9821a9440",
    "test_trial_suite_cleanup_guard.py": "4f6d652e45b4f19c82828ccabb80a278b89bb97e74484c49b19f2bc630ab1e6f",
    "test_trial_suite_cleanup_review.py": "e22de2df7ae6459e86e00d9e63da24ad36a93faf43f128374bb4d9234de0995a",
    "test_trial_suite_cli.py": "0982bb62ad80415e7076da2cfe2fd83cfea88d62fdd097f0d1da2c78e2241059",
    "test_trial_suite_cli_identity.py": "ba2bf0252043a2f3fa7e3aec21b8785d5c7568439e0eb1f76b486e42f75bd96f",
    "test_trial_suite_comparison.py": "1c5661f4943525b81182116e2441b26fd8938b3e8d41c1754c464ec21744bc53",
    "test_trial_suite_comparison_lineage.py": "b0f6b1a8bf4e5e785ca0d19a79dac35ec496e014282e8b8af34e55992f3d81a4",
    "test_trial_suite_docker.py": "e574fbaaaf77c22bce826711a6929ecaa9385ffa3288687129c2a76651c1e0df",
    "test_trial_suite_empty_baseline.py": "f26156c7cfab66408d5b5be2eb517f2b989d14e32a411f2663983fd4e27e7500",
    "test_trial_suite_entry_preservation.py": "e50bebfd1809fef5a8d17032248e62c77589de97856c9b6010f06b4b7db50562",
    "test_trial_suite_entry_selection.py": "7d05b4bf0e641ae40d89e0acad7233f5000733a8c16be2ae175ee1a6311692ec",
    "test_trial_suite_execution.py": "2111b197947352a8dd14c2ad643d75603f4a15c42d093e8add2381b6c33f6f6f",
    "test_trial_suite_git_quiescence.py": "c1f3e65d15fba576e5919c01f4834c5549ff9364b6129d72ac25e7b303c0928e",
    "test_trial_suite_inventory.py": "adf72d6df25437d81b8d19974a2831c64e8ba929d990342402f0769a62498115",
    "test_trial_suite_isolation.py": "4c1e96062ecfb9c20106330ac1ca2ca9cedeb59e7308cd0f46908f25d8eb34dd",
    "test_trial_suite_manifest.py": "70b3ee83f608643d5757df9f9c90c6be33ee041ef05e964e4f5072b535fc0f56",
    "test_trial_suite_matrix_cli.py": "2c9cd52323abaff0abe36b05e15032a1b00e9ea61c5db8b9a7fa8e74e7957dc5",
    "test_trial_suite_native_events.py": "9144ff9d34deec2b89cd16442bf70751d890dd63618e4674cb04a2de86eb53b7",
    "test_trial_suite_native_layout.py": "8444958e4633cd5d77f75a7caaa1f77122f07abe167612140fa197191d519be9",
    "test_trial_suite_native_lineage.py": "b1857c249eb6b511508df968e3317b04fc0acb7bc6f80fea181761a4fd90cfdf",
    "test_trial_suite_native_loading.py": "b43d8ed625b33f5285fc1248d94a17e26c6cb4c9bcf8cd0d6c036faa42535f95",
    "test_trial_suite_native_preflight.py": "3613732a70efeb0194a9175a78edc8a656a24ce6077852170fa5cd3d689c30be",
    "test_trial_suite_native_preparation.py": "6683e17ee2eedd5dc815d6b58fcae26f416898bab8f397c62a3794a748ca63fa",
    "test_trial_suite_native_runtime.py": "ecdff81fab5c32f61cb2f362191248c83bf8e4c4c9506a7f6adff8e24ee45dae",
    "test_trial_suite_probes.py": "6371d93ac2e624fe6052fc95cdb8400955d34b940c4182d9c94922c8eb43167b",
    "test_trial_suite_qualification.py": "09731949215b0bc81c39df5007157f3d5e2d44013ecb712e1a273943d2bb9279",
    "test_trial_suite_reservation.py": "592f898fa34b6421f33ff800c722f1377328d475b0d76d40853fdc4f12dcb86d",
    "test_trial_suite_resource_bounds.py": "b7d7cb09491b3d0774cd20f5687fb561ebbb5205200546e35ffc93e10d89bb9b",
    "test_trial_suite_review_boundaries.py": "62503ad6662248f1b59b7634af3050cc2e2f739d23339c80d20f2d114ac1d546",
    "test_trial_suite_setup_readback.py": "c43cece17a709237ed117d616a880b5217543f8489f8b8b641a181070d3359f8",
    "test_trial_suite_workflow.py": "37c2507a50a1faf7ec59d8cf837d20992ae96e754a2e0aee4abf97f06d8a864e",
    "test_trial_workspaces.py": "56f3cf95406268ec2dd6b413b1bf54ff1f6bca7f0f0b60fe9f8e3f80dc5b008d",
    "test_typescript_contract.py": "56505d33575982a78c65601b31e2791fd744e4e3de9a5652e2f1b30390ced3dc",
    "test_verification_contracts.py": "76bb8151b2e64010f25b1d7cc60a96f4b4acba65cb7da1c257140546e5977314",
    "test_verification_receipts.py": "db7cedc292d431ea18323d9da502c6d7adb9041159b9ad85289726de132acd0b",
    "test_verification_repositories.py": "f3b16d6c3a8a41eb4876dafe542e8d6a0e56c8e8cf33f6b93929765bfd3cd603",
    "test_verification_symlinks.py": "c722812b7b97b365765a0e33789d3c0c767ae6d2faf9785741fce66dbbb0a3a2",
    "test_workflow_admission.py": "a02a13a709a13c8553891a8431a0b7c11063cf72d9bb321f397d5e121b111cba",
    "test_workflow_efficiency.py": "e259443b0f4d4bc1ada9affba22f975c54f888dfc1e651f05ef01a93b0b3a209",
    "test_workflow_handoff.py": "df05ed94edf06bdd4b36c9bde177461d6da73ae23d6c92127c5d13b30ba5c3a2",
    "test_workflow_sequences.py": "7a9fd3be2278fc3f3f97739aa438cf73380fc92261ca42fa59a4d8dd72353672",
    "test_zagrosi_skills.py": "d2e0e0c6c4d1f2efb620ccadb0377473aa2f1898bb58d0b78b7cfbfa3c686cf0",
    "trial_suite_assessment_cases.py": "c9bc3b0c2e20b2d8e02bb9b7ce647feb1898ef1fd6d5830ffcc70f91a37a2826",
    "trial_suite_dependency_admission_cases.py": "b24d62b45cc0abc27e1ac9cf3bcbeb6c5ede894e4c300d2d6b7fdc78604c6e38",
    "trial_suite_fixtures.py": "a0b95fec633cbea667581cb940390da37e376c42b7de70d54cba279216c85a2e",
    "trial_suite_lineage_review_cases.py": "9ae97f6fc60f3296d4b4ebbca83a451329f2e52cc9276aa8b38c6b8fe8f27712",
    "trial_suite_observation_cases.py": "756d5a29c97f2b1e2e042fd1cb1a739aef1a60a02a0fd164b2c0bfa37a0065af",
    "trial_suite_observation_client_cases.py": "3231146344bbb66fa474d6860559523f064a945392738d0aa75f0f00a49d6cb3",
    "trial_suite_observation_client_review_cases.py": "3dfaf5889cf26c9127706fd9088f5b0f815fc41ff4167cd9e8876a7e5b77b125",
    "trial_suite_prepare_cases.py": "158fc4cf1d90d2a9b9ad9ba6a4db80ce89526e8d2be40e36905e2519890b09c7",
    "trial_suite_prepare_review_cases.py": "eef7f760bf04065245d1b254b29a074ef0a773da6db808c071be2738f4734c07"
}
# END TEST MANIFEST


def implementation_source_paths() -> dict[str, Path]:
    running_tool = _storage.absolute_path_no_follow(str(CLI_PATH))
    plugin_root = running_tool.parent.parent
    paths = {
        "tool": plugin_root / "scripts" / "zagrosi_skills.py",
        "skill": plugin_root / "skills" / "zagrosi-implement" / "SKILL.md",
        "test": plugin_root / "tests" / "test_zagrosi_skills.py",
    }
    if running_tool != paths["tool"]:
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            "Detached mode must run from the fixed scripts/zagrosi_skills.py plugin path.",
            implement_source="tool",
            expected_implement_source_path=str(paths["tool"]),
            actual_implement_source_path=str(running_tool),
        )
    return paths


def reopen_implementation_source(source: str, path: Path) -> dict[str, Any]:
    parent_fd: int | None = None
    reopened_parent_fd: int | None = None
    try:
        parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
        parent_stat = os.fstat(parent_fd)
        raw = _secure_io.read_single_link_regular_at(parent_fd, path.name, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        reopened_parent_fd = _secure_io.open_directory_chain_no_follow(path.parent)
        reopened_parent_stat = os.fstat(reopened_parent_fd)
        reopened_raw = _secure_io.read_single_link_regular_at(reopened_parent_fd, path.name, cap=_detached_contract.IMPLEMENTATION_SOURCE_CAP)
        if (
            (parent_stat.st_dev, parent_stat.st_ino) != (reopened_parent_stat.st_dev, reopened_parent_stat.st_ino)
            or raw != reopened_raw
        ):
            raise _models.DetachedImplementationError(
                "implement-source-changed",
                f"Implementation {source} source changed while its complete bytes were reopened.",
                implement_source=source,
                implement_source_path=str(path),
            )
        return {"path": str(path), "sha256": _handoff_wire.sha256_digest(raw), "size": len(raw)}
    except _models.DetachedImplementationError as exc:
        if exc.code == "implement-source-changed":
            raise
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            f"Implementation {source} source must be a component-wise no-follow regular single-link file.",
            implement_source=source,
            implement_source_path=str(path),
            source_error_code=exc.code,
        ) from exc
    except OSError as exc:
        raise _models.DetachedImplementationError(
            "unsafe-implement-source",
            f"Implementation {source} source could not be reopened safely.",
            implement_source=source,
            implement_source_path=str(path),
        ) from exc
    finally:
        if reopened_parent_fd is not None:
            os.close(reopened_parent_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def verify_detached_contract_reference(skill_path: Path) -> dict[str, Any]:
    contract_path = skill_path.parent / _detached_contract.DETACHED_CONTRACT_RELATIVE_PATH
    record = reopen_implementation_source("contract", contract_path)
    if record["sha256"] != _detached_contract.DETACHED_CONTRACT_SHA256:
        raise _models.DetachedImplementationError(
            "implement-contract-drift",
            "Detached implementation contract bytes do not match the tool-pinned complete-file sha256.",
            implement_source="contract",
            implement_source_path=record["path"],
            expected_implement_source_sha256=_detached_contract.DETACHED_CONTRACT_SHA256,
            actual_implement_source_sha256=record["sha256"],
        )
    return record


def expected_implementation_source_hashes(args: argparse.Namespace) -> dict[str, str]:
    expected: dict[str, str] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        argument = f"--expected-implement-{source}-sha256"
        value = getattr(args, f"expected_implement_{source}_sha256", None)
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise _models.DetachedImplementationError(
                "missing-implement-source-hash",
                f"Detached frozen-planning mode requires {argument} with an exact sha256 digest.",
                implement_source=source,
                required_argument=argument,
            )
        expected[source] = value
    return expected


def verify_implementation_tests(test_path: Path, anchor_record: dict[str, Any]) -> None:
    if not isinstance(TEST_MANIFEST, dict) or test_path.name not in TEST_MANIFEST or any(
        not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_]+\.py", name) is None
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for name, digest in TEST_MANIFEST.items()
    ):
        raise _models.DetachedImplementationError(
            "unsafe-implement-source", "Implementation test manifest is invalid.", implement_source="test",
        )
    parent_fd = _secure_io.open_directory_chain_no_follow(test_path.parent)
    try:
        for name, digest in TEST_MANIFEST.items():
            record = anchor_record if name == test_path.name else reopen_implementation_source("test", test_path.parent / name)
            if record["sha256"] != f"sha256:{digest}":
                raise _models.DetachedImplementationError(
                    "implement-source-drift", "Implementation test source no longer matches the tool-pinned manifest.",
                    implement_source="test", implement_source_path=record["path"],
                )
        reopened_fd = _secure_io.open_directory_chain_no_follow(test_path.parent)
        try:
            if _secure_io._fd_identity(reopened_fd) != _secure_io._fd_identity(parent_fd):
                raise _models.DetachedImplementationError(
                    "implement-source-changed", "Implementation test directory changed during source verification.",
                    implement_source="test", implement_source_path=str(test_path.parent),
                )
            if {name for name in os.listdir(reopened_fd) if name.endswith(".py")} != set(TEST_MANIFEST):
                raise _models.DetachedImplementationError(
                    "implement-source-drift", "Implementation test source inventory no longer matches the tool-pinned manifest.",
                    implement_source="test",
                )
        finally:
            os.close(reopened_fd)
    finally:
        os.close(parent_fd)


def reopen_implementation_sources(*, expected_hashes: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    try:
        verify_sources()
    except ImportError as exc:
        raise _models.DetachedImplementationError(
            "implement-source-drift", "Implementation runtime modules no longer match the tool-pinned manifest.",
            implement_source="tool",
        ) from exc
    records: dict[str, dict[str, Any]] = {}
    paths = implementation_source_paths()
    verify_detached_contract_reference(paths["skill"])
    for source, path in paths.items():
        record = reopen_implementation_source(source, path)
        expected_sha256 = expected_hashes.get(source) if expected_hashes is not None else None
        if expected_sha256 is not None and record["sha256"] != expected_sha256:
            raise _models.DetachedImplementationError(
                "implement-source-drift",
                f"Implementation {source} source bytes do not match the required complete-file sha256.",
                implement_source=source,
                implement_source_path=record["path"],
                expected_implement_source_sha256=expected_sha256,
                actual_implement_source_sha256=record["sha256"],
            )
        records[source] = record
    verify_implementation_tests(paths["test"], records["test"])
    return records


def implementation_source_config_fields(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        record = records[source]
        fields[f"implement_{source}_path"] = record["path"]
        fields[f"implement_{source}_sha256"] = record["sha256"]
        fields[f"implement_{source}_size"] = record["size"]
    return fields


def verify_implementation_sources(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paths = implementation_source_paths()
    verify_detached_contract_reference(paths["skill"])
    expected_hashes: dict[str, str] = {}
    for source in _detached_contract.IMPLEMENTATION_SOURCE_NAMES:
        path_field = f"implement_{source}_path"
        hash_field = f"implement_{source}_sha256"
        size_field = f"implement_{source}_size"
        expected_path = str(paths[source])
        expected_sha256 = config.get(hash_field)
        expected_size = config.get(size_field)
        if config.get(path_field) != expected_path:
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config does not bind the exact current implementation {source} source path.",
                implement_source=source,
                expected_implement_source_path=expected_path,
                actual_implement_source_path=config.get(path_field),
            )
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256):
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config implementation {source} source sha256 is invalid.",
                implement_source=source,
            )
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                f"Detached config implementation {source} source size is invalid.",
                implement_source=source,
            )
        expected_hashes[source] = expected_sha256
    records = reopen_implementation_sources(expected_hashes=expected_hashes)
    for source, record in records.items():
        expected_size = config[f"implement_{source}_size"]
        if record["size"] != expected_size:
            raise _models.DetachedImplementationError(
                "implement-source-drift",
                f"Implementation {source} source size no longer matches implement-setup.",
                implement_source=source,
                implement_source_path=record["path"],
                expected_implement_source_size=expected_size,
                actual_implement_source_size=record["size"],
            )
    return records
