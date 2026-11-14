"""Plain-language, grid-aware edit report; values are preliminary estimates."""
from datetime import datetime
import os
import math


def _same_path(first, second):
    """Treat equivalent Windows paths as the same DEM, regardless of slash style."""
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def write_report(path, result, settings, original_dem, baseline_dem, outputs):
    d = result['report_data']
    mode = settings.get('mode', 'flat')
    profile = {'terrain':'terrain following (mean cross-section samples)',
               'grade':'gradient-controlled terrain fit',
               'balanced':'median cross-section terrain fit with grade limit'}.get(settings.get('profile_method'), 'terrain following')
    cut, fill = d['cut_pixels'], d['fill_pixels']
    lines = [
        'DEM EDIT REPORT', '=' * 60,
        'Created: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        '', 'IN PLAIN LANGUAGE',
        'The plugin created a new DEM for a {}.'.format('terrain-following road' if mode == 'road' else 'flat platform'),
        'It lowered ground at {:,} grid cells and raised ground at {:,} grid cells.'.format(cut, fill),
        'Lowered area: {:.1f} square metres; raised area: {:.1f} square metres.'.format(d['cut_area'], d['fill_area']),
        'The deepest calculated cut is {:.2f}; the highest fill is {:.2f} DEM elevation units.'.format(result['max_cut'], result['max_fill']),
        'The areas and estimated volumes below include both the corridor and its outside transition.',
        '', 'TAKE CARE',
        'This is a preliminary terrain model. Soil, rock, drainage and slope stability are not determined from a DEM.',
        'Check road cross-sections and transitions visually, especially at bends and steep ground.',
        'Vertical units and vertical datum are not independently verified. Grade and cubic-metre estimates assume elevations are in metres.',
    ]
    if result.get('unmet_transition_pixels', 0):
        lines.append('The cut/fill transition still differed from the original ground at {} tested boundary cells. Increase the transition width or review the slopes.'.format(result['unmet_transition_pixels']))
        if _finite(result.get('max_unmet_transition_delta')):
            lines.append('The largest difference from original terrain at those tested boundary cells was {:.2f} DEM elevation units. This is not a complete boundary or stability assessment.'.format(result['max_unmet_transition_delta']))
        if _finite(result.get('estimated_tie_distance')):
            lines.append('At the tested edge cells, the greatest estimated distance to reach the sampled edge elevation is {:.1f} m from the nearest edited cell, assuming the ground beyond that edge is level. Actual terrain may require more or less distance; this is not a completed design.'.format(result['estimated_tie_distance']))
    if result.get('transition_unrepresented'):
        lines.append('No valid outside transition cells were sampled. The chosen transition width may be too small for this grid or the DEM may not cover the surrounding area.')
    if result.get('transition_edge_unchecked'):
        lines.append('No valid cells were available in the outer transition edge band, so the tie-in there was not checked. Inspect DEM coverage and NoData.')
    if result.get('ribbon_gap_pixels', 0):
        lines.append('{} road cells were not covered by adjacent cross-section triangles and used the nearest-line elevation instead. Review ends, bends and narrow sections.'.format(result['ribbon_gap_pixels']))
        if result.get('ribbon_endpoint_pixels', 0):
            lines.append('{} of those fallback cells project to a centerline endpoint; this classification does not certify the end areas.'.format(result['ribbon_endpoint_pixels']))
    if result.get('skipped_nodata_pixels', 0):
        lines.append('{} NoData cells in the corridor were left unchanged.'.format(result['skipped_nodata_pixels']))
    if result.get('outside_dem_area', 0) > 0:
        lines.append('Approximately {:.1f} square metres of the corridor lie outside the DEM and could not be edited.'.format(result['outside_dem_area']))
    if result.get('transition_outside_dem_area', 0) > 0:
        lines.append('The requested corridor and transition envelope extends approximately {:.1f} square metres beyond the DEM; the transition cannot be checked or completed there.'.format(result['transition_outside_dem_area']))
    if result.get('transition_nodata_pixels', 0):
        lines.append('{} NoData cells in the potential transition area were left unchanged.'.format(result['transition_nodata_pixels']))
    from .crs_check import crs_cautions
    for label, key in (('Project CRS', 'project_crs_review'), ('DEM CRS', 'dem_crs_review')):
        if isinstance(result.get(key), dict):
            lines.extend(crs_cautions(label, result[key]))
            if result[key].get('checked'):
                lines.append('{} estimated local grid-distance difference: {:.2f}% at one representative point; this does not certify distortion over the full corridor.'.format(label, result[key]['scale_error_pct']))
    if mode == 'flat' and _finite(result.get('flat_rounding_difference')) and abs(result['flat_rounding_difference']) >= 0.0005:
        lines.append('The output pixel type changed the platform elevation by {:.6f} DEM elevation units; the stored value is {:.6f}. Use a suitable floating-point output and check precision if this matters.'.format(result['flat_rounding_difference'], result['flat_output_elevation']))
    if max(result['max_cut'],result['max_fill']) > 5:
        lines.append('A cut or fill exceeds 5 DEM elevation units. Review the alignment, maximum grade and calculated profile; this is a review prompt, not an engineering limit.')
    if not _same_path(baseline_dem, original_dem):
        lines.append('The working DEM was interpolated. A finer grid does not restore missing terrain detail.')
    if settings.get('source') == 'polygon' and mode == 'road':
        lines.append('A polygon-derived centerline is approximate and may be smoothed inside the polygon. Check the route at branches, bends and narrow sections.')
        if settings.get('polygon_centerline_ambiguous'):
            lines.append('The polygon had branches, holes or competing routes. Only one suggested route determined the profile across the entire polygon; review the other areas carefully.')
    raster_grade = result.get('raster_max_grade')
    grade_spacing = result.get('raster_grade_spacing')
    sample_count = result.get('raster_grade_sample_count')
    exceedances = result.get('raster_grade_exceedances')
    crossfall = result.get('raster_crossfall_max')
    crossfall_samples = result.get('raster_crossfall_samples')
    crossfall_attempts = result.get('raster_crossfall_attempts')
    crossfall_exceedances = result.get('raster_crossfall_exceedances')
    crossfall_tolerance = result.get('crossfall_tolerance')
    turn_ranges = result.get('turn_plateau_ranges') or []
    if mode == 'road':
        if settings.get('profile_method', 'terrain') == 'terrain':
            lines.append('Terrain following does not apply a maximum road grade. Review the sampled grade and the entire alignment; choose a grade-controlled profile if a limit is needed.')
        if _finite(raster_grade):
            if isinstance(exceedances, int) and exceedances > 0:
                lines.append('Output-array grade samples exceeded the requested limit on {} intervals. Inspect the grade along the complete road.'.format(exceedances))
            lines.append('Output-array grades are sampled estimates, not a survey or continuous guarantee. Narrow grids and interpolating within road cells can affect the estimate.')
            if isinstance(result.get('raster_grade_attempts'), int) and isinstance(sample_count, int) and sample_count < result['raster_grade_attempts']:
                lines.append('Only {} of {} attempted grade intervals could be measured; review the missing road sections.'.format(sample_count, result['raster_grade_attempts']))
        else:
            lines.append('The output-array road grade was not measured. The internal profile grade alone does not guarantee the grade of the output DEM.')
        if _finite(crossfall) and isinstance(crossfall_samples, int) and crossfall_samples > 0:
            if isinstance(crossfall_exceedances, int) and crossfall_exceedances > 0:
                lines.append('{} sampled road cross-sections exceeded the configured elevation-range check. Inspect these sections in QGIS.'.format(crossfall_exceedances))
                lines.append('Raster interpolation can mix neighboring road stations, especially at bends or abrupt grade changes. A flagged range is a review indicator and does not by itself prove a cross-road defect.')
                flagged = result.get('crossfall_flagged_sections') or []
                if flagged:
                    lines.append('Largest sampled cross-road ranges (approximate distance along the input or suggested centerline):')
                    for chainage, difference in flagged:
                        lines.append('  {:.1f} m: {:.3f} DEM elevation units'.format(chainage, difference))
                    if crossfall_exceedances > len(flagged):
                        lines.append('  Showing {} of {} flagged sampled sections.'.format(len(flagged), crossfall_exceedances))
            if isinstance(crossfall_attempts, int) and crossfall_samples < crossfall_attempts:
                lines.append('Only {} of {} attempted cross-sections had all required valid samples; review the missing sections.'.format(crossfall_samples, crossfall_attempts))
            lines.append('Cross-section checks use masked output-array samples across the middle 20% to 80% of selected sections, before the raster is written. Missing sections and unsampled locations are not proven flat; narrow grids limit the check.')
        else:
            lines.append('Output-array cross-section flatness was not measured. The road surface is not verified to be flat across its width.')
        if turn_ranges:
            lines.append('The calculated profile was deliberately levelled along {} section(s) at sharp turns or where curved road sections conflicted. This can make even a terrain-following road depart from existing ground and increase cut or fill; review the affected sections.'.format(len(turn_ranges)))
            lines.append('Approximate levelled centerline distances (profile adjustments can extend beyond these ranges):')
            for start, end in turn_ranges:
                lines.append('  {:.2f} to {:.2f} m'.format(start, end))
    if mode == 'road' and settings.get('source') != 'polygon':
        width = float(settings.get('distance', 0)) * (2 if settings.get('side') == 'both' else 1)
        cells = width / max(d['pixel_x'], d['pixel_y']) if max(d['pixel_x'], d['pixel_y']) else 0
        if cells < 5:
            lines.append('Road width spans approximately {:.1f} grid cells. Fewer than five cells can make bends and edges appear rough; interpolation does not add terrain detail.'.format(cells))
    lines += ['', 'WHAT WAS DONE', 'Input DEM: ' + original_dem,
              'Comparison baseline: ' + baseline_dem,
              'Edited DEM: ' + outputs['dem'],
              'Road/corridor source: ' + str(settings.get('source', 'unknown')),
              'Mode: ' + mode,
              'Method: ' + (profile if mode == 'road' else 'single elevation: ' + str(settings.get('flat_source','value'))),
              'Platform elevation calculated: {}; value stored: {}'.format(result.get('applied_value', 'not recorded'), result.get('flat_output_elevation', 'not recorded')) if mode == 'flat' else 'Road elevation varies along its profile.',
              'Profile smoothing: {} m{}'.format(settings.get('smooth_m',0), ' (automatic window)' if not settings.get('smooth_m', 0) else ''),
              'Requested maximum grade: {}%'.format(settings.get('maximum_grade','not set')) if mode == 'road' and settings.get('profile_method') in ('grade','balanced') else 'Maximum grade setting: not applied',
              'Grade limit used to fit the internal profile: {:.2f}% (may be lower than requested after checking raster samples).'.format(result['fitted_grade_limit']) if _finite(result.get('fitted_grade_limit')) else 'Internal grade fitting limit: not applicable or not recorded',
              'Maximum internal profile grade before rasterization: {:.2f}%'.format(result.get('profile_max_grade', result.get('max_grade'))) if _finite(result.get('profile_max_grade', result.get('max_grade'))) else 'Internal profile grade: not available',
              'Maximum output-array sampled grade: {:.2f}% (median interval: {} m; {} valid of {} attempted intervals; {} intervals above requested grade)'.format(raster_grade, grade_spacing if grade_spacing is not None else 'not recorded', sample_count if sample_count is not None else 'not recorded', result.get('raster_grade_attempts', 'not recorded'), exceedances if exceedances is not None else 'not applicable') if _finite(raster_grade) else 'Output-array sampled grade: not available',
              'Grade is sampled at road-section midpoints using road cells only; missing intervals are skipped. Measurements are calculated before writing and do not certify the reopened raster or unsampled locations.' if mode == 'road' else 'Road grade check: not applicable to a flat-area edit',
              'Grade samples are flagged when they exceed the requested limit by more than 0.1 percentage points; the fitted profile may be adjusted to reduce sampled exceedances.' if mode == 'road' and settings.get('profile_method') in ('grade', 'balanced') else 'No requested-grade exceedance check applies to this mode.',
              'Maximum sampled cross-section elevation range: {:.3f} DEM elevation units; {} valid of {} attempted stations; {} exceeded the {}-unit check.'.format(crossfall, crossfall_samples, crossfall_attempts if crossfall_attempts is not None else 'unknown', crossfall_exceedances if crossfall_exceedances is not None else 'unknown', crossfall_tolerance if crossfall_tolerance is not None else 'unrecorded') if mode == 'road' and _finite(crossfall) and isinstance(crossfall_samples, int) and crossfall_samples > 0 else 'Output-array cross-section check: not available' if mode == 'road' else 'Road cross-section check: not applicable to a flat-area edit',
              'Cross-section check threshold is a configurable QA indicator, not an engineering standard. Measurements sample the middle 20%–80% of selected sections; not all road locations are checked.' if mode == 'road' else 'Flat-area elevations should be checked against the requested elevation after raster output.',
              'Buffer side: {}; distance per selected side: {} m'.format(settings.get('side','n/a'),settings.get('distance','n/a')) if settings.get('source') != 'polygon' else 'Road width: determined by selected polygon; width can vary.',
              'Outside transition limit: {} m; cut slope V/H: {}; fill slope V/H: {}'.format(settings.get('feather_m',0), settings.get('cut_slope',1), settings.get('fill_slope',0.5)),
              '', 'DEM COMPARISON',
              'Output grid: {:.2f} m by {:.2f} m; one cell covers {:.2f} square metres.'.format(d['pixel_x'],d['pixel_y'],d['pixel_area']),
              'Within the road/platform corridor, baseline elevations range {:.2f} to {:.2f}; edited elevations range {:.2f} to {:.2f} DEM elevation units.'.format(d['source_min'],d['source_max'],d['result_min'],d['result_max']),
              'Estimated cut: {:.1f}; estimated fill: {:.1f} square metres times DEM elevation unit.'.format(d['cut_volume'],d['fill_volume']),
              'These estimates equal cubic metres only if DEM elevations are in metres. Vertical units and datum are not verified. Estimates sum grid-cell area times elevation change; compaction, shrinkage and material movement are excluded.',
              'Changes of at most 0.000001 DEM elevation units are excluded from cut/fill counts and totals.',
              'Cut and fill are computed for the entire edited footprint, including the road surface and transition; they are not an engineered earthwork balance.',
              'Output precision: {}'.format('floating-point' if settings.get('output_dtype') == 'float' else 'source raster data type (integer values may be rounded)' if settings.get('output_dtype') == 'source' else 'not recorded'),
              'Difference raster: ' + (outputs.get('difference') or 'not requested') + ' (positive = fill; negative = cut).',
              'The comparison uses matching cells on the output grid. The outside transition is included in whole-edit totals.',
              '', 'ROAD SECTIONS',
              'Distances are approximate metres from the start of the centerline. Sections include the road corridor and outside transition; transition cells are assigned to their nearest road cell.',
    ]
    sections=d.get('sections',[])
    if not sections: lines.append('No road sections: this was a flat-area edit or section data was unavailable.')
    else:
        for start,end,cut_max,fill_max,count in sections:
            lines.append('{:.0f}–{:.0f} m: largest cut {:.2f}, largest fill {:.2f} DEM elevation units ({} corridor or transition cells).'.format(start,end,cut_max,fill_max,count))
    lines += ['', 'OUTPUT FILES']
    for label,p in outputs.items():
        if p: lines.append(label+': '+p)
    lines += ['', 'All cut/fill figures depend on the input DEM, raster resolution and chosen settings.', '']
    with open(path,'x',encoding='utf-8',newline='\n') as f: f.write('\n'.join(lines))
    return path
