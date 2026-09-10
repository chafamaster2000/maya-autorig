def main(**kwargs):
    import prep_mesh
    mesh, info = prep_mesh.prepare(**kwargs)
    info.update({"success": True, "mesh": mesh})
    return info
