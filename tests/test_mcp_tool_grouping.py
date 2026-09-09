from __future__ import annotations

from aida.mcp.tool_grouping import group_tool_names

# The real ~110 tool names pyIrena-mcp registers (pyirena/mcp/server.py),
# the motivating case for planning/mcp_tool_scaling.md Tier 1. Hardcoded
# rather than imported since pyIrena is a separate, optional dependency —
# this is a regression fixture, not a live contract check.
PYIRENA_TOOL_NAMES = [
    "pyirena_ctrl_add_unified_level",
    "pyirena_ctrl_check_level_feasibility",
    "pyirena_ctrl_close_session",
    "pyirena_ctrl_detect_features",
    "pyirena_ctrl_export_fit_report",
    "pyirena_ctrl_fix_all_except",
    "pyirena_ctrl_fix_parameter",
    "pyirena_ctrl_free_parameter",
    "pyirena_ctrl_get_chi_squared",
    "pyirena_ctrl_get_data_q_range",
    "pyirena_ctrl_get_fit_image",
    "pyirena_ctrl_get_fit_q_range",
    "pyirena_ctrl_get_fit_quality",
    "pyirena_ctrl_get_level_options",
    "pyirena_ctrl_get_model_description",
    "pyirena_ctrl_get_model_parameters",
    "pyirena_ctrl_get_residuals",
    "pyirena_ctrl_get_residuals_image",
    "pyirena_ctrl_get_session_summary",
    "pyirena_ctrl_list_available_models",
    "pyirena_ctrl_list_open_sessions",
    "pyirena_ctrl_modeling_add_population",
    "pyirena_ctrl_modeling_get_config",
    "pyirena_ctrl_modeling_get_fit_image",
    "pyirena_ctrl_modeling_get_population_parameters",
    "pyirena_ctrl_modeling_get_results",
    "pyirena_ctrl_modeling_list_population_types",
    "pyirena_ctrl_modeling_list_populations",
    "pyirena_ctrl_modeling_remove_population",
    "pyirena_ctrl_modeling_run_fit",
    "pyirena_ctrl_modeling_save_fit",
    "pyirena_ctrl_modeling_select_model",
    "pyirena_ctrl_modeling_set_background",
    "pyirena_ctrl_modeling_set_population_enabled",
    "pyirena_ctrl_modeling_set_population_option",
    "pyirena_ctrl_modeling_set_population_parameter",
    "pyirena_ctrl_modeling_set_population_parameter_bounds",
    "pyirena_ctrl_modeling_set_population_parameter_fit",
    "pyirena_ctrl_modeling_set_q_range",
    "pyirena_ctrl_open_dataset",
    "pyirena_ctrl_remove_unified_level",
    "pyirena_ctrl_reset_fit_q_range",
    "pyirena_ctrl_reset_parameters_to_defaults",
    "pyirena_ctrl_run_fit",
    "pyirena_ctrl_save_fit",
    "pyirena_ctrl_select_model",
    "pyirena_ctrl_set_fit_q_range",
    "pyirena_ctrl_set_level_option",
    "pyirena_ctrl_set_parameter_bounds",
    "pyirena_ctrl_set_parameter_value",
    "pyirena_ctrl_simple_fix_parameter",
    "pyirena_ctrl_simple_free_parameter",
    "pyirena_ctrl_simple_get_config",
    "pyirena_ctrl_simple_get_fit_image",
    "pyirena_ctrl_simple_get_linearization_image",
    "pyirena_ctrl_simple_get_parameters",
    "pyirena_ctrl_simple_get_results",
    "pyirena_ctrl_simple_list_models",
    "pyirena_ctrl_simple_reset_parameters",
    "pyirena_ctrl_simple_run_fit",
    "pyirena_ctrl_simple_save_fit",
    "pyirena_ctrl_simple_select_model",
    "pyirena_ctrl_simple_set_background",
    "pyirena_ctrl_simple_set_parameter",
    "pyirena_ctrl_simple_set_parameter_bounds",
    "pyirena_ctrl_sizes_fit_flat_background",
    "pyirena_ctrl_sizes_fit_power_law_background",
    "pyirena_ctrl_sizes_get_background_image",
    "pyirena_ctrl_sizes_get_config",
    "pyirena_ctrl_sizes_get_distribution",
    "pyirena_ctrl_sizes_get_fit_image",
    "pyirena_ctrl_sizes_get_results",
    "pyirena_ctrl_sizes_run_fit",
    "pyirena_ctrl_sizes_save_fit",
    "pyirena_ctrl_sizes_select_model",
    "pyirena_ctrl_sizes_set_background",
    "pyirena_ctrl_sizes_set_error_handling",
    "pyirena_ctrl_sizes_set_method",
    "pyirena_ctrl_sizes_set_shape",
    "pyirena_ctrl_sizes_set_size_grid",
    "pyirena_ctrl_sizes_suggest_setup",
    "pyirena_ctrl_waxs_add_peak",
    "pyirena_ctrl_waxs_find_peaks",
    "pyirena_ctrl_waxs_get_config",
    "pyirena_ctrl_waxs_get_fit_image",
    "pyirena_ctrl_waxs_get_peak_parameters",
    "pyirena_ctrl_waxs_get_results",
    "pyirena_ctrl_waxs_list_options",
    "pyirena_ctrl_waxs_list_peaks",
    "pyirena_ctrl_waxs_remove_peak",
    "pyirena_ctrl_waxs_run_fit",
    "pyirena_ctrl_waxs_save_fit",
    "pyirena_ctrl_waxs_select_model",
    "pyirena_ctrl_waxs_set_background",
    "pyirena_ctrl_waxs_set_background_parameter",
    "pyirena_ctrl_waxs_set_peak_parameter",
    "pyirena_ctrl_waxs_set_peak_parameter_bounds",
    "pyirena_ctrl_waxs_set_peak_parameter_fit",
    "pyirena_ctrl_waxs_set_peak_shape",
    "pyirena_inspect_file",
    "pyirena_list_files",
    "pyirena_plot_iq",
    "pyirena_plot_parameter_trend",
    "pyirena_read_fractals",
    "pyirena_read_manipulation_provenance",
    "pyirena_read_merge_provenance",
    "pyirena_read_metadata",
    "pyirena_read_modeling",
    "pyirena_read_reduced_data",
    "pyirena_read_saxs_morph",
    "pyirena_read_simple_fit",
    "pyirena_read_size_distribution",
    "pyirena_read_unified_fit",
    "pyirena_read_waxs_peakfit",
    "pyirena_summarize_folder",
    "pyirena_summarize_sample",
    "pyirena_tabulate_parameter",
]


def test_few_tools_stay_ungrouped():
    names = [f"echo_{i}" for i in range(5)]
    groups = group_tool_names(names)
    assert len(groups) == 1
    assert groups[0].name is None
    assert groups[0].tools == sorted(names)


def test_uniform_names_with_no_branching_structure_stay_flat():
    """tool_0..tool_149 (the existing GUI scroll-area test's fixture) share
    only the 'tool' prefix and no further common structure below it — every
    remaining token is a unique leaf, so this must resolve back to a single
    ungrouped section rather than 150 one-tool 'categories'."""
    names = [f"tool_{i}" for i in range(150)]
    groups = group_tool_names(names)
    assert len(groups) == 1
    assert groups[0].name is None
    assert len(groups[0].tools) == 150


def test_pyirena_tool_names_group_by_fit_type():
    groups = group_tool_names(PYIRENA_TOOL_NAMES)
    by_name = {g.name: g.tools for g in groups}

    # The four fit-type categories the taxonomy is actually meant to
    # separate must come out distinctly, with every one of their real
    # tools accounted for.
    assert set(by_name["ctrl/waxs"]) == {
        n for n in PYIRENA_TOOL_NAMES if n.startswith("pyirena_ctrl_waxs_")
    }
    assert set(by_name["ctrl/sizes"]) == {
        n for n in PYIRENA_TOOL_NAMES if n.startswith("pyirena_ctrl_sizes_")
    }
    assert set(by_name["ctrl/simple"]) == {
        n for n in PYIRENA_TOOL_NAMES if n.startswith("pyirena_ctrl_simple_")
    }
    assert set(by_name["ctrl/modeling"]) == {
        n for n in PYIRENA_TOOL_NAMES if n.startswith("pyirena_ctrl_modeling_")
    }

    # Never all-one-bucket, never one-bucket-per-tool.
    assert 2 < len(groups) < len(PYIRENA_TOOL_NAMES) / 2

    # No tool is lost or duplicated across groups.
    all_grouped = [tool for g in groups for tool in g.tools]
    assert sorted(all_grouped) == sorted(PYIRENA_TOOL_NAMES)


def test_no_group_is_anywhere_near_the_original_wall_of_checkboxes():
    """The whole point is keeping any one category small enough to look at
    without re-triggering the original ~110-checkbox problem. The depth
    cap means a leaf group can end up a bit above MIN_TOOLS_TO_GROUP (e.g.
    ctrl/waxs, at 18), but nowhere near the original flat count."""
    groups = group_tool_names(PYIRENA_TOOL_NAMES)
    assert max(len(g.tools) for g in groups) < len(PYIRENA_TOOL_NAMES) / 4
