##############################################################################
#
#   MRC FGU CGAT
#
#   $Id$
#
#   Copyright (C) 2017 Tom Smith
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
###############################################################################
"""===========================
Pipeline cell barcode
===========================

:Author: Tom Smith
:Release: $Id$
:Date: |today|
:Tags: Python


Overview
========

This pipeline performs the analysis for the manuscript reporting on
the impact of reference transcriptome choice on quantification accuracy and differnential expression analyses

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general
information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.ini` file.
CGATReport report requires a :file:`conf.py` and optionally a
:file:`cgatreport.ini` file (see :ref:`PipelineReporting`).

Default configuration files can be generated by executing:

   python <srcdir>/pipeline_ref_transcriptome_paper.py config

Input files
-----------

None required except the pipeline configuration files.

Requirements
------------

The pipeline requires the results from
:doc:`pipeline_annotations`. Set the configuration variable
:py:data:`annotations_database` and :py:data:`annotations_dir`.

On top of the default CGAT setup, the pipeline requires the following
software to be in the path:

.. Add any additional external requirements such as 3rd party software
   or R modules below:

Requirements:

* samtools >= 1.1

Pipeline output
===============

.. Describe output files of the pipeline here

Glossary
========

.. glossary::


Code
====

"""

# import standard modules
import sys
import os
import sqlite3
import pysam
import time
import collections
import CGAT.IOTools as IOTools

# import pipeline decorators
from ruffus import *
from ruffus.combinatorics import *

# CGAT code imports
import CGAT.Experiment as E

# CGATPipeline imports
import CGATPipelines.Pipeline as P
import CGATPipelines.PipelineMapping as PipelineMapping

# Import utility function from pipeline module file

# load options from the config file
PARAMS = P.getParameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"])

# add configuration values from associated pipelines
#
# 1. pipeline_annotations: any parameters will be added with the
#    prefix "annotations_". The interface will be updated with
#    "annotations_dir" to point to the absolute path names.
PARAMS.update(P.peekParameters(
    PARAMS["hg_annotations_dir"],
    "pipeline_annotations.py",
    on_error_raise=__name__ == "__main__",
    prefix="hg_annotations_",
    update_interface=True))

PARAMS.update(P.peekParameters(
    PARAMS["mm_annotations_dir"],
    "pipeline_annotations.py",
    on_error_raise=__name__ == "__main__",
    prefix="mmx_annotations_",
    update_interface=True))


# if necessary, update the PARAMS dictionary in any modules file.
# e.g.:
#
# import CGATPipelines.PipelineGeneset as PipelineGeneset
# PipelineGeneset.PARAMS = PARAMS
#
# Note that this is a hack and deprecated, better pass all
# parameters that are needed by a function explicitely.

# -----------------------------------------------
# Utility functions
def connect():
    '''utility function to connect to database.

    Use this method to connect to the pipeline database.
    Additional databases can be attached here as well.

    Returns an sqlite3 database handle.
    '''

    dbh = sqlite3.connect(PARAMS["database_name"])
    statement = '''ATTACH DATABASE '%s' as annotations''' % (
        PARAMS["annotations_database"])
    cc = dbh.cursor()
    cc.execute(statement)
    cc.close()

    return dbh

# ---------------------------------------------------
# Specific pipeline tasks

##############################################################################
#  Download raw data
##############################################################################

@mkdir('raw')
@originate('raw/dropseq_mixed_species.fastq.1.gz')
def downloadDropSeq(outfile):
    ''' Download the DropSeq data'''

    sample_basename = outfile.replace(".fastq.1.gz", "")
    outfile2 = sample_basename + ".fastq.2.gz"
    name2ID = {"mixed_species": "SRR1873277"}

    url_prefix = "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR187/007/"

    sample_id = name2ID[os.path.basename(sample_basename)]

    statement = '''
    wget %(url_prefix)s/%(sample_id)s/%(sample_id)s_1.fastq.gz -O %(outfile)s; 
    wget %(url_prefix)s/%(sample_id)s/%(sample_id)s_2.fastq.gz -O %(outfile2)s
    '''

    P.run()

@mkdir('raw')
@originate('raw/indrop_es_1.fastq.1.gz')
def downloadInDrop(outfile):
    ''' Download the InDrop data'''

    sample_basename = outfile.replace(".fastq.1.gz", "")
    outfile2 = sample_basename + ".fastq.2.gz"
    name2ID = {"indrop_es_1": "SRR1784310"}

    url_prefix = "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR178/000/"

    sample_id = name2ID[os.path.basename(sample_basename)]

    statement = '''
    wget %(url_prefix)s/%(sample_id)s/%(sample_id)s_1.fastq.gz -O %(outfile)s; 
    wget %(url_prefix)s/%(sample_id)s/%(sample_id)s_2.fastq.gz -O %(outfile2)s
    '''

    P.run()



# need maps from sample to:
# * cell ranger version - determine download url
# * number of cells - whitelisting
# * species - alignment and gene assignment tasks
# this info is stored in sample_info in pipeline src dir.

TENX2INFO = collections.defaultdict(lambda: collections.defaultdict())
TENX_DATASETS = set()
with IOTools.openFile(PARAMS['sample_info'], "r") as inf:
    header = next(inf)
    for line in inf:
        sample_name, r_version, n_cells, species, seq_sat, chem = line.strip().split(",")
        if chem == "v2":  # can't currently handle the v1 data (Where are the UMIs?!)
            TENX_DATASETS.add(sample_name)
            TENX2INFO[sample_name]["version"] = r_version
            TENX2INFO[sample_name]["n_cells"] = n_cells
            TENX2INFO[sample_name]["species"] = species
            TENX2INFO[sample_name]["chem"] = chem

# restrict for testing (pbmc8k has >700M reads! = 75GB fastqs!!)
#TENX_DATASETS = [x for x in TENX_DATASETS if x != "pbmc8k"] 


@mkdir('raw', 'raw/10X_fastqs/')
@originate(['raw/10X_fastqs/%s.fastq.1.gz' % x for x in TENX_DATASETS])
def download10x(outfile):
    ''' Download the 10X data, untar, cat the fastqs and delete
    the unwanted files'''

    sample_name = os.path.basename(outfile).replace(".fastq.1.gz", "")
    
    ranger_version = TENX2INFO[sample_name]["version"]

    tar_file = "%s_fastqs.tar" % sample_name

    url = "http://s3-us-west-2.amazonaws.com/10x.files/samples/cell-exp/%s/%s/%s" % (
        ranger_version, sample_name, tar_file)

    tmp_dir = "10x_%s_fastqs" % sample_name
    outfile2 = outfile.replace(".fastq.1.gz", ".fastq.2.gz")

    statement = '''
    mkdir %(tmp_dir)s; checkpoint ;
    wget %(url)s; checkpoint ;
    tar -xf %(tar_file)s -C %(tmp_dir)s; checkpoint ;
    cat %(tmp_dir)s/*/%(sample_name)s_S1_*_R1_001.fastq.gz
    > %(outfile)s; checkpoint ;
    cat %(tmp_dir)s/*/%(sample_name)s_S1_*_R2_001.fastq.gz
    > %(outfile2)s; checkpoint ;
    rm -rf %(tmp_dir)s %(tar_file)s

    '''
    P.run()



@follows(downloadDropSeq,
         downloadInDrop,
         download10x)
def DownloadData():
    pass


##############################################################################
#  Extract barcodes
##############################################################################

@mkdir(("whitelist"))
@transform(downloadDropSeq,
           regex("raw/(\S+).fastq.1.gz"),
           r"whitelist/\1_whitelist.tsv")
def MakeDropSeqWhitelist(infile, outfile):
    'make a whitelist of "true" cell barcodes'

    statement = '''
    umi_tools whitelist  --bc-pattern=CCCCCCCCCCCCNNNNNNNN
    --extract-method=string --plot-prefix=%(outfile)s
    -I %(infile)s -L %(outfile)s.log --subset-reads=10000000
    -S %(outfile)s
    '''

    P.run()

@mkdir("extract")
@transform(downloadDropSeq,
           regex("raw/(\S+).fastq.1.gz"),
           r"extract/\1_extracted.fastq")
def ExtractDropSeq(infile, outfile):
    'extract the umi and cell barcodes'

    infile2 = infile.replace("fastq.1.gz", "fastq.2.gz")

    statement = '''
    umi_tools extract 
    --bc-pattern=CCCCCCCCCCCCNNNNNNNN
    --extract-method=string 
    -I %(infile)s
    --read2-in=%(infile2)s
    -L %(outfile)s.log
    --read2-stdout
    -S %(outfile)s
    '''

    P.run()

@mkdir(("whitelist"))
@transform(downloadInDrop,
           regex("raw/(\S+).fastq.1.gz"),
           r"whitelist/\1_whitelist.tsv")
def MakeInDropWhitelist(infile, outfile):
    'make a whitelist of "true" cell barcodes'

    statement = '''
    umi_tools whitelist --extract-method=regex -I %(infile)s 
    --bc-pattern="(?P<cell_1>.{8,12})(?P<discard_2>GAGTGATTGCTTGTGACGCCTT)(?P<cell_3>.{8})(?P<umi_1>.{6})T{3}.*"
    --plot-prefix=%(outfile)s -L %(outfile)s.log
    --subset-reads=10000000
    -S %(outfile)s
    '''

    P.run()

@mkdir("extract")
@transform(downloadInDrop,
           regex("raw/(\S+).fastq.1.gz"),
           r"extract/\1_extracted.fastq.gz")
def ExtractInDrop(infile, outfile):
    'extract the umi and cell barcodes'

    infile2 = infile.replace("fastq.1.gz", "fastq.2.gz")

    statement = '''
    umi_tools extract 
    --bc-pattern="(?P<cell_1>.{8,12})(?P<discard_2>GAGTGATTGCTTGTGACGCCTT)(?P<cell_3>.{8})(?P<umi_1>.{6})T{3}.*"
    --extract-method=regex
    -I %(infile)s
    --read2-in=%(infile2)s
    -L %(outfile)s.log
    --read2-stdout 
    -S %(outfile)s
    '''

    P.run()


@mkdir(("whitelist"))
@transform(download10x,
           regex("raw/10X_fastqs/(\S+).fastq.1.gz"),
           r"whitelist/10X_\1_whitelist.tsv")
def Make10XWhitelist(infile, outfile):
    'make a whitelist of "true" cell barcodes'

    sample_name = os.path.basename(infile).replace(".fastq.1.gz", "")

    n_cells = TENX2INFO[sample_name]["n_cells"]
    
    statement = '''
    umi_tools whitelist 
    --bc-pattern=CCCCCCCCCCCCCCCCNNNNNNNNNN
    --extract-method=string
    --plot-prefix=%(outfile)s
    -I %(infile)s
    -L %(outfile)s.log
    --subset-reads=10000000
    --set-cell-number=%(n_cells)s
    -S %(outfile)s
    '''

    P.run()


@mkdir("extract")
@follows(Make10XWhitelist)
@transform(download10x,
           regex("raw/10X_fastqs/(\S+).fastq.1.gz"),
           add_inputs(r"whitelist/10X_\1_whitelist.tsv"),
           r"extract/\1_extracted.fastq")
def Extract10X(infiles, outfile):
    '''extract the umi and cell barcodes using the whitelist defined
    using a manual threshold'''
    
    infile, whitelist = infiles

    infile2 = infile.replace("fastq.1.gz", "fastq.2.gz")

    statement = '''
    umi_tools extract 
    --bc-pattern=CCCCCCCCCCCCCCCCNNNNNNNNNN
    --extract-method=string
    -I %(infile)s
    --read2-in=%(infile2)s
    -L %(outfile)s.log
    --filter-cell-barcode
    --whitelist=%(whitelist)s
    --read2-stdout
    -S %(outfile)s
    '''

    P.run()
    IOTools.zapFile(infile)
    IOTools.zapFile(infile2)
    P.touch(outfile)

@follows(MakeDropSeqWhitelist, MakeInDropWhitelist,
         Make10XWhitelist)
def MakeWhitelists():
    pass

@follows(ExtractDropSeq, ExtractInDrop,
         Extract10X)
def Extract():
    pass



##############################################################################
#  Make merged references
##############################################################################

@mkdir("references.dir")
@originate("references.dir/merged_hg_mm_genome.fasta")
def MakeMergedGenomes(outfile):

    hg_genome_file = os.path.abspath(
        os.path.join(PARAMS["genome_dir"], PARAMS["genome_hg"] + ".fa"))

    mm_genome_file = os.path.abspath(
        os.path.join(PARAMS["genome_dir"], PARAMS["genome_mm"] + ".fa"))

    statement = '''
    sed 's/>chr/>hg_chr/g' %(hg_genome_file)s > %(outfile)s; checkpoint; 
    sed 's/>chr/>mm_chr/g' %(mm_genome_file)s >> %(outfile)s; checkpoint;
    samtools faidx %(outfile)s
    '''
    
    P.run()


@mkdir("references.dir")
@merge((PARAMS['geneset_hg'], PARAMS['geneset_mm']),
       "references.dir/merged_hg_mm_transcriptome.gtf.gz")
def MakeMergedGTF(infiles, outfile):

    hg_infile, mm_infile = infiles

    statement = '''
    zcat %(hg_infile)s | awk '$3=="exon"' | sed 's/^chr/hg_chr/g' |
    gzip > %(outfile)s; checkpoint; 
    zcat %(mm_infile)s | awk '$3=="exon"' | sed 's/^chr/hg_chr/g' |
    gzip >> %(outfile)s; '''
    
    P.run()

# not currently req.
@mkdir("references.dir")
@merge((PARAMS['geneset_hg'], PARAMS['geneset_mm']),
       "references.dir/merged_hg_mm_transcriptome.fasta")
def MakeMergedTranscriptome(infiles, outfile):

    hg_infile, mm_infile = infiles

    hg_genome_file = os.path.abspath(
        os.path.join(PARAMS["genome_dir"], PARAMS["genome_hg"] + ".fa"))

    mm_genome_file = os.path.abspath(
        os.path.join(PARAMS["genome_dir"], PARAMS["genome_mm"] + ".fa"))

    statement = '''
    zcat %(hg_infile)s |
    awk '$3=="exon"'|
    cgat gff2fasta
    --is-gtf --genome-file=%(hg_genome_file)s --fold-at=60 -v 0
    --log=%(outfile)s.log > %(outfile)s;
    checkpoint;
    zcat %(mm_infile)s |
    awk '$3=="exon"'|
    cgat gff2fasta
    --is-gtf --genome-file=%(mm_genome_file)s --fold-at=60 -v 0
    --log=%(outfile)s.log >> %(outfile)s;
    checkpoint;
    samtools faidx %(outfile)s
    '''

    P.run()


##############################################################################
#  Build Indexes
##############################################################################
@transform(MakeMergedGenomes, 
           regex("(\S+).fasta"),
           r"\1.1.ht2l")
def IndexMergedGenomes(infile, outfile):

    outfile_base = outfile.replace(".1.ht2l", "")

    statement = '''
    hisat2-build %(infile)s %(outfile_base)s >%(outfile)s.log
    '''

    P.run()

@transform(MakeMergedGenomes, 
           regex("(\S+).fasta"),
           add_inputs(MakeMergedGTF),
           r"\1_star_index/SA")
def STARIndexMergedGenomes(infiles, outfile):
    
    genome, gtf = infiles

    outfile_base = outfile.replace(".1.ht2l", "")
    strIndexPath = os.path.dirname(outfile)

    job_memory = "60G"

    statement = '''
    mkdir %(strIndexPath)s; checkpoint; 
    STAR --runMode genomeGenerate
    --runThreadN %(star_threads)s
    --genomeDir %(strIndexPath)s
    --outFileNamePrefix %(strIndexPath)s
    --genomeFastaFiles %(genome)s
    --limitGenomeGenerateRAM 60000000000
    --genomeChrBinNbits 12
    --sjdbGTFfile %(gtf)s
    --sjdbOverhang %(star_tx_overhang)s'''

    P.run()

##############################################################################
#  Align to genomes
##############################################################################

@mkdir("mapped")
@transform(Extract10X,
           regex("extract/(\S+)_extracted.fastq"),
           add_inputs(IndexMergedGenomes),
           r"mapped/\1.bam")
def AlignToHumanMouse(infiles, outfile):
    '''
    align 10X data to individual or combined hg38 & mm10 genome
    retain only the mapped, primary reads
    '''

    # combined_genome only req. for subset of infiles
    infile, combined_genome = infiles

    sample_name = os.path.basename(infile).replace("_extracted.fastq", "")
    species = TENX2INFO[sample_name]["species"]
    
    if species in ["mm", "hg"]:
        if species == "mm":
            species = "mm10"
        elif species == "hg":
            species = "hg38"
        ref_genome = os.path.join(PARAMS["hisat_dir"], species)
    else:
        ref_genome = combined_genome.replace(".1.ht2l", "")  

    unsorted_bam = P.getTempFilename()

    job_threads = 12
    job_memory = "3.9G"

    statement = '''
    hisat2 -x %(ref_genome)s -U %(infile)s -k1 --threads %(job_threads)s
    2>%(outfile)s.log |
    samtools view -bS -F 4 -F 256 - > %(unsorted_bam)s; checkpoint ;
    samtools sort %(unsorted_bam)s -o %(outfile)s; checkpoint ; 
    samtools index %(outfile)s; checkpoint; 
    rm -f %(unsorted_bam)s'''

    P.run()

    IOTools.zapFile(infile)
    P.touch(outfile)

@follows(AlignToHumanMouse)
def Align():
    pass


##############################################################################
#  Assign genes
##############################################################################

@transform(AlignToHumanMouse,
           regex("(\S+).bam"),
           add_inputs((PARAMS['geneset_hg'], PARAMS['geneset_mm'])),
           r"\1.bam.featureCounts.bam")
def AssignGenes10X(infiles, outfile):
    '''
    '''

    infile, genesets = infiles
    hg_geneset, mm_geneset = genesets

    tmpgeneset = P.getTempFilename()

    sample_name = os.path.basename(infile).replace(".bam", "")
    species = TENX2INFO[sample_name]["species"]

    if species == "hg":
        statement = '''
        zcat %(hg_geneset)s  > %(tmpgeneset)s; checkpoint ;
        '''

    elif species == "mm":
        statement = '''
        zcat %(mm_geneset)s  > %(tmpgeneset)s; checkpoint ;
        '''

    elif species == "hgmm":
        statement = '''
        zcat %(hg_geneset)s | sed 's/^chr/hg_chr/g' > %(tmpgeneset)s; checkpoint ;
        zcat %(mm_geneset)s | sed 's/^chr/mm_chr/g' >> %(tmpgeneset)s; checkpoint ;
        '''
    
    elif species == "ercc":
        raise ValueError("pipeline can't handle ercc yet: %s" % sample_name)
    
    else:
        raise ValueError("species not recognised for sample: %s" % sample_name)


    # -M assigns multimapped reads too
    # -R BAM outputs tagged BAM to "[INFILENAME].featurecounts.bam"
    statement += '''
    featureCounts -a %(tmpgeneset)s -M -R BAM -o %(outfile)s.txt %(infile)s
    > %(outfile)s.log; checkpoint; 
    rm -f %(tmpgeneset)s; '''

    P.run()
    IOTools.zapFile(infile)
    P.touch(outfile)


##############################################################################
#  Group
##############################################################################

# group command assumes all the mapping locations have been
# retained. This is not the case currently! (DON'T RUN THE TASK
# BELOW!)
@transform(AssignGenes10X,
           regex("(\S+)_hg_mm.bam.featureCounts.bam"),
           r"\1_hg_mm_grouped.bam")
def group10X(infile, outfile):
    '''
    run UMI-tools group to get the UMI groups per gene per cell
    '''

    tmpfile = P.getTempDir()
    outfile_base = os.path.basename(outfile)

    statement = '''
    samtools sort %(infile)s -o %(tmpfile)s/%(outfile_base)s; checkpoint ;
    samtools index %(tmpfile)s/%(outfile_base)s; checkpoint ;
    umi_tools group -I %(tmpfile)s/%(outfile_base)s
    --per-cell --per-gene --gene-tag=XT
    --group-out=%(outfile)s.tsv --output-bam
    --log=%(outfile)s.log
    --no-sort-output
    > %(outfile)s; checkpoint ;
    samtools index %(outfile)s ;
    rm -r %(tmpfile)s ;
    '''

    P.run()

@transform(AssignGenes10X,
           regex("(\S+).bam.featureCounts.bam"),
           r"\1_dedup.bam")
def UMIToolsDedup10X(infile, outfile):
    '''
    run UMI-tools dedup to get the UMI groups per gene per cell
    '''

    tmpfile = P.getTempDir(shared=True)
    outfile_base = os.path.basename(outfile)

    statement = '''
    samtools sort %(infile)s -o %(tmpfile)s/%(outfile_base)s; checkpoint ;
    samtools index %(tmpfile)s/%(outfile_base)s; checkpoint ;
    umi_tools dedup -I %(tmpfile)s/%(outfile_base)s
    --per-cell --per-gene --gene-tag=XT
    --log=%(outfile)s.log
    --no-sort-output
    --output-stats=%(outfile)s_stats
    > %(outfile)s; checkpoint; 
    rm -r %(tmpfile)s ;
    '''

    P.run()


##############################################################################
#  Deduplicate
##############################################################################
@transform(group10X,
           regex("(\S+)_hg_mm_grouped.bam"),
           r"\1_hg_mm_deduped.bam")
def dedup10X(infile, outfile):
    '''
    remove duplicate reads using UMI groups
    '''

    # need to write some bespoke code to take in output of UMI-tools
    # group and remove duplicate reads
    pass

##############################################################################
#  Add CB errors (Post alignment)
##############################################################################


# these could take in all extracted data (e.g indrop and dropset too)
@mkdir("add_cb_errors.dir")
@transform(AlignToHumanMouse,
           regex("mapped/(\S+)_hg_mm.bam"),
           r"add_cb_errors.dir/\1_added_errors.bam")
def Add10XCBErrors(infile, outfile): 
    '''add errors to the CBs for the 10X data'''
    
    statement = '''
    python %(script_dir)s/add_cb_errors.py --infile %(infile)s
    --log=%(outfile)s.log --error-table=%(outfile)s_table.tsv
    --outfile %(outfile)s'''

    P.run()


@mkdir("add_cb_errors.dir")
@transform(Extract10X,
           regex("extract/(\S+)_extracted_set.fastq.gz"),
           r"add_cb_errors.dir/\1_added_errors_high.fastq.gz")
def Add10XCBErrorsHigh(infile, outfile): 
    '''add errors to the CBs for the 10X data'''
    
    statement = '''
    python %(script_dir)s/add_cb_errors.py --infile %(infile)s
    --log=%(outfile)s.log --error-table=%(outfile)s_table.tsv
    --error_method=literature-high
    --outfile %(outfile)s'''

    P.run()


@mkdir("add_cb_errors.dir")
@transform(Extract10X,
           regex("extract/(\S+)_extracted_set.fastq.gz"),
           r"add_cb_errors.dir/\1_added_errors_constant_low.fastq.gz")
def Add10XCBErrorsConstantLow(infile, outfile): 
    '''add errors to the CBs for the 10X data'''
    
    statement = '''
    python %(script_dir)s/add_cb_errors.py --infile %(infile)s
    --log=%(outfile)s.log --error-table=%(outfile)s_table.tsv
    --error_method=constant
    --sub-rate=0.001 --insert-rate=0.00002 --delete-rate=0.00001
    --outfile %(outfile)s'''

    P.run()


@transform(Extract10X,
           regex("extract/(\S+)_extracted_set.fastq.gz"),
           r"add_cb_errors.dir/\1_added_errors_constant_low.fastq.gz")
def Add10XCBErrorsConstantHigh(infile, outfile): 
    '''add errors to the CBs for the 10X data'''

    statement = '''
    python %(script_dir)s/add_cb_errors.py --infile %(infile)s
    --log=%(outfile)s.log --error-table=%(outfile)s_table.tsv
    --error_method=constant
    --sub-rate=0.01 --insert-rate=0.002 --delete-rate=0.001
    --outfile %(outfile)s'''

    P.run()


@follows(Add10XCBErrors, Add10XCBErrorsHigh,
         Add10XCBErrorsConstantLow, Add10XCBErrorsConstantHigh)
def addErrors():
    pass




##############################################################################
# Generic pipeline tasks                                                     #
##############################################################################
@follows(MakeWhitelists)
def full():
    pass


@follows(mkdir("report"))
def build_report():
    '''build report from scratch.

    Any existing report will be overwritten.
    '''

    E.info("starting report build process from scratch")
    P.run_report(clean=True)


@follows(mkdir("report"))
def update_report():
    '''update report.

    This will update a report with any changes inside the report
    document or code. Note that updates to the data will not cause
    relevant sections to be updated. Use the cgatreport-clean utility
    first.
    '''

    E.info("updating report")
    P.run_report(clean=False)


@follows(update_report)
def publish_report():
    '''publish report in the CGAT downloads directory.'''

    E.info("publishing report")
    P.publish_report()

if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
