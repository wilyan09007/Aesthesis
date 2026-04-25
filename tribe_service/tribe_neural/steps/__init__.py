"""TRIBE pipeline steps.

Each module is a single deterministic transformation:
    step1_tribe_video  : MP4 path -> (preds: ndarray[(n_TRs, 20484)], segments)
    step2_roi          : preds + masks + weights -> dict[roi_name -> ndarray(n_TRs,)]
    step3_stats        : 1-D timeseries -> 11-feature stats dict
    step4_connectivity : roi_ts -> dict[pair_name -> Pearson r]
    step5_composites   : per-TR + window composite formulas
    step7_timeline     : assembles the full /process_video_timeline response
"""
