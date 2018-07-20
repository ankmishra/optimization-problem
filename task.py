import magic
import pandas as pd

from itertools import islice

from .models import Snp, File, UserProfile, Gene, UserRsid, UserGeneReputation
from . import utils
import os


def check_genotype_style(genotype, snp):
    genotype = genotype.strip()
    if len(genotype) > 1:
        if genotype[0] != genotype[1]:
            return "heterozygous"
        elif genotype[0] == genotype[1]:
            if genotype[0] == snp.minor_allele:
                return "homozygous_minor"
            else:
                return "homozygous_major"
    elif len(genotype) == 1:
        if genotype[0] != snp.minor_allele:
            return "hemizygous_major"
        else:
            return "hemizygous_minor"
    elif genotype == 'II':
        return "double_insertion"
    elif genotype == 'DD':
        return "double_deletion"
    elif genotype == 'I':
        return "insertion"
    elif genotype == 'D':
        return "deletion"
    elif genotype == '--' or genotype == '-' or genotype == 'NC' or genotype == "":
        return "unknown"


def get_results(user_rsid, snp, file):
    zygosities = {
        'heterozygous': ('Heterozygous', '+/-', 'O',),
        'homozygous_major': ('Homozygous Major', '-/-', 'G',),
        'homozygous_minor': ('Homozygous Minor', '+/+', 'B',),
        'hemizygous_major': ('Hemizygous Major', '-', 'G',),
        'hemizygous_minor': ('Hemizygous Minor', '+', 'B',),
        'unknown': ('Unknown', '?/?', 'U',),
        'double_insertion': ('Double Insertion', 'I/I', 'O',),
        'double_deletion': ('Double Deletion', 'D/D', 'O',),
        'insertion': ('Single Insertion', 'I', 'O',),
        'deletion': ('Single Deletion', 'D', 'O',),
    }
    genotype_style_falls = {
        "heterozygous": "heterozygous_color",
        "insertion": "heterozygous_color",
        "deletion": "heterozygous_color",
        "double_insertion": "heterozygous_color",
        "double_deletion": "heterozygous_color",

        "homozygous_minor": "homozygous_minor_color",
        "hemizygous_minor": "homozygous_minor_color",
        "minor": "homozygous_minor_color",

        "homozygous_major": "homozygous_major_color",
        "hemizygous_major": "homozygous_major_color",
        "major": "homozygous_major_color",
    }
    colors = {
        'green': ('G', 'Good', 'success',),
        'yellow': ('O', 'Okay', 'warning',),
        'red': ('B', 'Bad', 'danger',),
        'gray': ('U', 'Unknown', 'default',),
    }

    color_field = genotype_style_falls[user_rsid.genotype_style]
    color = getattr(snp, color_field, "").strip()

    if color:
        reputation = colors.get(color)[0]
    else:
        reputation = zygosities.get(user_rsid.genotype_style)[2]
    return (
        reputation,
        user_rsid.genotype_style,
    )


def calculate_total_reputation(file):
    # MULTIPLIER - If reputation of the genotype is good (G), multiplier is 0,
    # if reputation is slightly bad, multiplier is 0.5, if reputation is bad (R), multiplier is 1
    mul = {
        "G": 0,
        "U": 0,
        "O": 0.5,
        "B": 1,
    }

    # The multipliers for all the different zygosities, i.e.heterozygous (one bad allele)
    # multiplies the score by 1, homozygous minor (two bad alleles) multiplies the score by 1.5
    z_mul = {
        'heterozygous': 1.2,
        'homozygous_major': 1,
        'major': 1,
        'homozygous_minor': 1.5,
        'minor': 1.5,
        'hemizygous_major': 1,
        'hemizygous_minor': 1.3,
        'unknown': 1,
        'double_insertion': 1.3,
        'double_deletion': 1.3,
        'insertion': 1.2,
        'deletion': 1.2,
    }

    # Spread amplifies the score, the higher the spread, the greater the score difference will be
    spread = 1.3

    print("Calculate genes reputation")
    rsids = file.related_rsid.values_list("rsid", flat=True).distinct()
    genes = Gene.objects.filter(snps__rsid__in=rsids).distinct().all()
    genes_count = genes.count()
    file.set_total_points(genes_count, latency=200)
    for gene in genes:
        total_reputation = 0
        for snp in gene.snps.all():
            user_rsid = file.related_rsid.filter(rsid=snp.rsid).first()
            if user_rsid is None:
                continue

            rep, zygosity = get_results(user_rsid, snp, file)

            if not rep or rep == "G" or rep == "U":
                continue

            importance = snp.importance

            weighted_reputation = importance * mul[rep]

            if rep == "B":
                weighted_reputation *= z_mul[zygosity]

            #  Now we apply the spread amplifier, we raise the score to the power of the spread number
            rep_square = pow(spread, weighted_reputation)
            total_reputation += rep_square
        try:
            UserGeneReputation.objects.create(gene=gene, file=file, score=total_reputation)
        except:
            pass
        file.update_progress()


def detect_service(file):
    # line = file.readline().decode()
    mime = magic.from_file(file)
    if mime == 'ASCII text, with very long lines':
        return File.SERVICE_VCF
    else:
        lines = list(islice(open(file), 0, 50))
        line = lines[0]
        if not isinstance(line, str):
            line = line.decode()
        text = " ".join([(l if isinstance(l, str) else l.decode()) for l in lines])

        if "23andMe" in line or "# rsid\tchromosome\tposition\tgenotype" in text:
            return File.SERVICE_23ANDME
        elif "AncestryDNA" in line:
            return File.SERVICE_ANCESTRY
        elif "Courtangen" in line or "rsid\tchromosome\tposition\tgenotype" in text:
            return File.SERVICE_COURTAGEN
        elif "RSID,CHROMOSOME,POSITION,RESULT" in line:
            return File.SERVICE_FAMILY_TREE
        elif "fileformat=VCF" in line:
            return File.SERVICE_VCF
        return File.SERVICE_UNKNOWN


def get_data_to_file(user_id, file_pk):
    user = User.objects.get(id=user_id)
    obj = File.allfileobjects.get(pk=file_pk)
    file_name = obj.original_name
    static_dir = 'staticfiles/'

    try:
        print(static_dir + file_name)
        data = get_s3_data_to_file(file_name)
        print("finished writing to file")
        print("Unzipping file...")
        archive, file, name = unzip_any_file(static_dir + file_name)
        print("Successfully unzipped")
        # name = name or file_name

        print("Detecting file service...")
        service = detect_service(file)
        obj.status = File.FILE_STATUS_PROCESSING
        obj.service = service
        obj.save()
        #
        return archive, file, name, user, obj
    except:
        handle_errors(obj)
        return None


def process_rsid_file(df, obj):
    p_total = len(df) + Gene.objects.count()
    obj.set_total_points(p_total, latency=100)
    print("Process file...")
    for index, item in df.iterrows():
        rsid = item[0]
        genotype = item[3]
        snp = Snp.objects.filter(rsid=rsid).first()
        if snp is not None:
            user_rsid = UserRsid.objects.filter(file=obj, rsid=rsid).first()
            if not user_rsid:
                UserRsid.objects.create(
                    rsid=rsid,
                    genotype=genotype,
                    file=obj,
                    genotype_style=check_genotype_style(genotype, snp)
                )
            else:
                user_rsid.genotype_style = check_genotype_style(genotype, snp)
                user_rsid.genotype = genotype
                user_rsid.save()
        # Updates the file processing progress
        obj.update_progress()


def upload(archive, file, name, user, obj, dashboard_uri):
    print('tasks.upload Start processing file %s' % name)

    df = pd.read_csv(file, nrows=1, header=None)
    try:
        obj.sequenced_at = df.ix[0, 0][df.ix[0, 0].index(":") + 2:]
    except:
        pass

    if archive:
        file = archive.open(name)

    # Slice to first (Header) row
    # for row in file:
    #     row = row.decode()
    #     if row.startswith('# rsid'):
    #         break

    df = pd.read_csv(file, header=0, delimiter="\t", dtype={"# rsid": str, "chromosome": str, "position": str,
                                                            "genotype": str})  # combine the upload with adding headers to speed it up

    df.columns = ["rsid", "chromosome", "position", "genotype"]

    process_rsid_file(df, obj)

    calculate_total_reputation(obj)

    print('tasks.upload Finished processing file %s' % name)


def upload_ancestry(archive, file, name, user, obj, dashboard_uri):
    try:
        print('tasks.upload_ancestry Start processing file %s' % name)

        df = pd.read_csv(file, header=0, comment='#', delimiter="\t",
                         dtype={"rsid": str, "chromosome": str, "position": str, "allele1": str,
                                "allele2": str})  # combine the upload with adding headers to speed it up

        df["genotype"] = df["allele1"] + df["allele2"]
        df = df[["rsid", "chromosome", "position", "genotype"]]

        process_rsid_file(df, obj)

        calculate_total_reputation(obj)

        print('tasks.upload_ancestry Finished processing file %s' % name)
    except Exception:
        utils.handle_errors(obj)


@app.task
def process_genome_file(user_id, dashboard_uri, file_pk, is_rescan=False):
    print("Start processing genome file...")
    services = {
        File.SERVICE_23ANDME: {"function": upload, "service": File.SERVICE_23ANDME},
        File.SERVICE_ANCESTRY: {"function": upload_ancestry, "service": File.SERVICE_ANCESTRY},
    }

    print("Download file from s3...")
    result = get_data_to_file(user_id, file_pk)
    if not result:
        return
    archive, file, name, user, obj = result
    try:
        if obj.service == File.SERVICE_UNKNOWN:
            line = file.readline().decode()
            raise Exception("Could not identify service, first line: %s" % line)
        print("Service detected as: %s" % obj.get_service_display())
        service = services.get(obj.service)
        fn = service.get("function")
        fn(archive, file, name, user, obj, dashboard_uri)

        obj.rescan_available = True
        # Updates the file processing progress to 100%
        obj.update_progress(100)

        utils.send_completed_email(dashboard_uri, user, obj)

        original_file = file[:-13]
        if os.path.isfile(file):
            os.remove(file)
        if os.path.isfile(file):
            os.remove(original_file)

    except Exception:
        utils.handle_errors(obj)
