def convert_mem_limit_to_bytes(mem_limit: str) -> int:
    if mem_limit.endswith("g"):
        return int(mem_limit.replace("g", "")) * 1024 * 1024 * 1024
    elif mem_limit.endswith("m"):
        return int(mem_limit.replace("m", "")) * 1024 * 1024
    elif mem_limit.endswith("k"):
        return int(mem_limit.replace("k", "")) * 1024
    elif mem_limit.endswith("b"):
        return int(mem_limit.replace("b", ""))
    else:
        raise ValueError(f"Invalid memory limit: {mem_limit}")
