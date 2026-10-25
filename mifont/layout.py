"""Infer usable font metrics from source body positions, retaining overhangs.

Metrics are estimates when only specimen images are available. Ink outside a
glyph's advance (italic tops, entry strokes and descenders) remains in its
outline; its full bounding-box width must not be used as extra letter spacing.
"""
from __future__ import annotations

from copy import deepcopy
import math
from statistics import median

try:
    from .font_export import normalize_glyph
except ImportError:
    from font_export import normalize_glyph


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _body_bounds(region,baseline,reference,slope):
    runs=region.get('ink_runs')
    if not isinstance(runs,list) or not runs:return None
    lower=region.get('char','').islower()
    top=baseline-reference*(.60 if lower else .82)
    bottom=baseline-reference*.10
    selected=[run for run in runs if isinstance(run,(list,tuple)) and len(run)==3
              and all(_finite(v) for v in run) and top<=run[0]<=bottom and run[1]<run[2]]
    if not selected:
        selected=[run for run in runs if isinstance(run,(list,tuple)) and len(run)==3
                  and all(_finite(v) for v in run) and run[1]<run[2]]
    if not selected:return None
    return (min(left+slope*(row-baseline) for row,left,right in selected),
            max(right+slope*(row-baseline) for row,left,right in selected))


def annotate_line_layout(regions,line,slope=0):
    """Return regions with source-coordinate origins and advances.

    Exact interior seam anchors supplied by segmentation take precedence.
    Independent glyphs use aligned body extents and local specimen gaps;
    excessively loose chart spacing is bounded. Source outline pixels are
    untouched. Call before tracing; apply_source_layout uses the same anchors.
    """
    result=[dict(region) for region in regions]
    baseline=line.get('baseline_y');reference=line.get('reference_height')
    if not _finite(baseline) or not _finite(reference) or reference<=0 or not _finite(slope):return result
    bounds=[_body_bounds(region,baseline,reference,slope) for region in result]
    usable=[i for i,value in enumerate(bounds) if value]
    if not usable:return result
    gaps=[bounds[b][0]-bounds[a][1] for a,b in zip(usable,usable[1:])]
    positive=[gap for gap in gaps if 0<=gap<=reference*.35]
    typical=median(positive) if positive else reference*.025
    # A specimen sheet may track glyphs widely. That is not intrinsic advance.
    connected=any(region.get('cut_approximate') for region in result)
    cap=reference*(.06 if connected else .12)
    gap=max(reference*.008,min(cap,typical))
    for position,index in enumerate(usable):
        region=result[index];left,right=bounds[index]
        before=gaps[position-1] if position else gap
        after=gaps[position] if position<len(gaps) else gap
        before=max(0,min(cap,before));after=max(0,min(cap,after))
        origin=left-before/2;end=right+after/2
        old_origin=region.get('layout_origin_x');old_advance=region.get('layout_advance_px')
        if _finite(old_origin) and _finite(old_advance) and old_advance>0:
            if not region.get('layout_outer_start'):origin=old_origin
            if not region.get('layout_outer_end'):end=old_origin+old_advance
        if end-origin<=reference*.025:continue
        region.update(layout_origin_x=origin,layout_advance_px=end-origin,
                      layout_method='source-body-and-seam-baselines-v1',layout_slant=slope)
    return result


def apply_source_layout(record,region):
    """Translate a traced outline to its source baseline origin; never rescale it."""
    origin=region.get('layout_origin_x');advance=region.get('layout_advance_px')
    reference=region.get('reference_height');box=region.get('box')
    if not (_finite(origin) and _finite(advance) and advance>0 and _finite(reference) and reference>0
            and isinstance(box,(list,tuple)) and len(box)==4 and _finite(box[0])):
        return record
    scale=700/reference
    # extract_glyph puts the tight crop's leftmost ink at its 40-unit bearing.
    # Move that same ink to the source baseline origin, allowing real overhangs.
    shift=(box[0]-origin)*scale-40
    value=deepcopy(record)
    value['recording']=[[operation,[[point[0]+shift,point[1]] if point is not None else None
                                   for point in points]] for operation,points in record['recording']]
    value['advance_width']=max(1,round(advance*scale))
    value.setdefault('provenance',{})['layout']={
        'method':region.get('layout_method','source-seam-baseline'),
        'origin_x':origin,'advance_px':advance,'approximate':True}
    return normalize_glyph(value)
