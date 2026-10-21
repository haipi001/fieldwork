"""Canonical bounded candidate sets; never silently truncate an explicit choice."""
def bounded_ids(value):
    if (not isinstance(value,list) or not 1<=len(value)<=3
        or any(not isinstance(item,str) or not 1<=len(item)<=100 for item in value)
        or len(set(value))!=len(value)):
        raise ValueError('Select one to three distinct candidate IDs')
    return sorted(value)
