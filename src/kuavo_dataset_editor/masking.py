"""Portable per-dimension label validity; no dependency on a training framework."""
import json
from pathlib import Path

import numpy as np

REASONS = {
    0: 'valid', 1: 'topic_absent', 2: 'sample_gap_or_stale',
    3: 'invalid_message_or_value', 4: 'rack_calibration_absent',
    5: 'no_reliable_multi_tag_anchor', 6: 'recorded_tf_unavailable',
}


def encode_missing(values, topic_present=True):
    """0 is a placeholder only when the corresponding bool is False."""
    values = np.asarray(values, dtype=np.float32)
    valid = np.isfinite(values)
    reasons = np.where(valid, 0, 2 if topic_present else 1).astype(np.uint8)
    return np.where(valid, values, 0).astype(np.float32), valid, reasons


def mask_mapping(features, declared=None):
    result = dict(declared or {})
    for key in features:
        if key.startswith('validity.') and key[9:] in features:
            result[key[9:]] = key
    return result


def visible_values(row, features, declared=None):
    """GUI must show a gap, NOT a zero curve, for excluded dimensions."""
    result = {key: [float(x) if np.isfinite(x) else None for x in np.asarray(value).reshape(-1)]
              for key,value in row.items() if np.asarray(value).dtype.kind in 'fibu'}
    for key,mask_key in mask_mapping(features, declared).items():
        if key not in result:
            continue
        mask = np.asarray(row.get(mask_key, [False]), dtype=bool).reshape(-1)
        if mask.size not in (1,len(result[key])):
            raise ValueError('validity dimension mismatch: '+key)
        mask = np.broadcast_to(mask,(len(result[key]),))
        result[key] = [value if good else None for value,good in zip(result[key],mask)]
    return result


def chunk_loss_mask(validity_chunk, padding_mask):
    """AND an already gathered (B,T,D) validity chunk with model padding mask.

    Explicit bridge only: callers must gather validity at the SAME future frame
    indices as action, then apply this after their processor creates its mask.
    Refuse broadcasting a current-frame mask over the future horizon.
    Returns numpy; a trainer can convert to its tensor dtype/device.
    """
    valid, padding = np.asarray(validity_chunk), np.asarray(padding_mask)
    if valid.ndim != 3 or padding.ndim != 3 or valid.shape[:2] != padding.shape[:2]:
        raise ValueError('mask must be (batch, horizon, dimensions), without horizon broadcasting')
    if valid.shape[2] > padding.shape[2] or not np.isin(valid,[0,1]).all() or not np.isin(padding,[0,1]).all():
        raise ValueError('masks must contain only 0/1 and fit model action dimensions')
    result = np.zeros_like(padding,dtype=bool)
    result[:,:,:valid.shape[2]] = valid.astype(bool) & padding[:,:,:valid.shape[2]].astype(bool)
    return result


def masked_statistics(values, valid):
    """Exact label-only statistics. Neutral fallback is NOT a measured statistic."""
    values,valid = np.asarray(values),np.asarray(valid,dtype=bool)
    if values.shape != valid.shape or values.ndim != 2:
        raise ValueError('statistics need matching (frames, dimensions) values and validity')
    stats = {key:[] for key in ('min','max','mean','std','q01','q10','q50','q90','q99')}
    counts = valid.sum(axis=0).astype(int).tolist()
    for i,count in enumerate(counts):
        column = values[valid[:,i],i].astype(np.float64)
        if count and not np.isfinite(column).all():
            raise ValueError('valid label contains non-finite values')
        if count:
            summary = dict(min=column.min(),max=column.max(),mean=column.mean(),std=column.std())
            summary.update(zip(('q01','q10','q50','q90','q99'),np.quantile(column,[.01,.1,.5,.9,.99])))
        else:
            summary = dict(min=-1,max=1,mean=0,std=1,q01=-1,q10=-1,q50=0,q90=1,q99=1)
        for key in stats:
            stats[key].append(float(summary[key]))
    # LeRobot count is a scalar frame count; true per-dimension counts live in
    # the sidecar. Do not misrepresent the scalar as valid label counts.
    stats['count'] = [len(values)]
    return stats,counts


def finalize_mask_metadata(root):
    """Read numeric parquet only, never re-decode videos. Safe on append/re-edit.

    Recompute global AND episode stats, so placeholders cannot enter normalization
    via a later episode filter or an append writer's aggregate_stats.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from .sources import write_json
    root = Path(root)
    info = json.loads((root/'meta/info.json').read_text())
    mapping = mask_mapping(info['features'])
    if not mapping:
        return {}
    keys = [key for key in mapping if info['features'][key]['dtype'] in ('float32','float64')]
    columns = ['episode_index', *keys, *(mapping[key] for key in keys)]
    blocks = {key:[] for key in columns}
    for path in sorted((root/'data').rglob('*.parquet')):
        table = pq.read_table(path,columns=columns)
        for key in columns:
            column = table[key].combine_chunks()
            if key=='episode_index':
                blocks[key].append(column.to_numpy())
            else:
                width = int(np.prod(info['features'][key]['shape']))
                blocks[key].append(column.flatten().to_numpy(zero_copy_only=False).reshape(-1,width))
    arrays = {key:np.concatenate(value) for key,value in blocks.items()}
    global_stats = json.loads((root/'meta/stats.json').read_text())
    summary,episode_stats = {},{}
    for key in keys:
        valid = arrays[mapping[key]]
        if valid.shape[1]==1:
            valid = np.broadcast_to(valid,arrays[key].shape)
        stats,counts = masked_statistics(arrays[key],valid)
        global_stats[key] = stats
        summary[key] = dict(names=info['features'][key].get('names'),valid_counts=counts,
                            frames=len(valid),masked_counts=(len(valid)-np.asarray(counts)).tolist(),
                            no_label_dimensions=np.flatnonzero(np.asarray(counts)==0).tolist(),
                            all_missing_stats='neutral fallback only: mean=0,std=1,min=-1,max=1')
        for episode in np.unique(arrays['episode_index']):
            selected = arrays['episode_index']==episode
            ep_stats,_ = masked_statistics(arrays[key][selected],valid[selected])
            episode_stats.setdefault(int(episode),{})[key] = ep_stats
    write_json(root/'meta/stats.json',global_stats)
    for path in sorted((root/'meta/episodes').rglob('*.parquet')):
        table = pq.read_table(path)
        rows = table.to_pylist()
        for row in rows:
            for feature,stats in episode_stats[int(row['episode_index'])].items():
                for name,value in stats.items():
                    column = f'stats/{feature}/{name}'
                    if column in row:
                        row[column] = value
        pq.write_table(pa.Table.from_pylist(rows,schema=table.schema),path)
    write_json(root/'editor/mask_summary.json',{'features':summary,'reason_codes':REASONS,
               'validity_semantics':'true/1 = included; false/0 = unknown target/input, not a zero command',
               'training':'Explicit trainer integration required; input validity is NOT an action loss mask.'})
    return summary
